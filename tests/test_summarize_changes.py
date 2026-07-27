"""Unit tests for orchestrator/code_generator/nodes/summarize_changes.py."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch


def _mock_llm(content: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    return resp


def _base_state(**overrides):
    state = {
        "ticket_key": "AOS-291",
        "work_plan_data": {
            "summary": "Split code gen into two nodes",
            "approach": "Add a summariser after generate_code",
            "tasks": [
                {"id": 1, "description": "Add finalizer script"},
                {"id": 2, "description": "Add summarize_changes node"},
            ],
        },
        "raw_results": {
            "ticket_key": "AOS-291",
            "branch": "feature/AOS-291+split",
            "commit_sha": "abc123",
            "files_changed": ["foo.py", "bar.py"],
            "build": "pass",
            "tests": "pass",
            "pr_url": "",
            "status": "success",
            "diff_base_sha": "deadbeef",
        },
        "working_dir": "/tmp/does-not-exist",  # git diff will return "" gracefully
        "reasoning_path": "",
    }
    state.update(overrides)
    return state


def test_summarize_changes_happy_path_uses_llm_description(monkeypatch):
    from orchestrator.code_generator.nodes import summarize_changes as node_mod

    monkeypatch.setenv("GOOSE_MODEL", "openai/gpt-5-mini")
    with patch.object(
        node_mod.litellm, "completion", return_value=_mock_llm("This PR adds foo and bar.")
    ) as mock_completion:
        result = node_mod.summarize_changes(_base_state())

    summary = result["code_generation_summary"]
    assert summary["description"] == "This PR adds foo and bar."
    # Structured fields from raw_results are preserved verbatim.
    assert summary["status"] == "success"
    assert summary["branch"] == "feature/AOS-291+split"
    assert summary["files_changed"] == ["foo.py", "bar.py"]
    # Internal-only field must not leak into the consumer-facing dict.
    assert "diff_base_sha" not in summary
    assert mock_completion.called


def test_summarize_changes_falls_back_on_llm_error(monkeypatch):
    """LLM failures must never fail the workflow — deterministic fallback runs."""
    from orchestrator.code_generator.nodes import summarize_changes as node_mod

    monkeypatch.setenv("GOOSE_MODEL", "openai/gpt-5-mini")
    with patch.object(node_mod.litellm, "completion", side_effect=RuntimeError("boom")):
        result = node_mod.summarize_changes(_base_state())

    summary = result["code_generation_summary"]
    # Fallback description synthesises from work_plan tasks + files_changed.
    assert "Split code gen into two nodes" in summary["description"]
    assert "foo.py" in summary["description"]
    assert "bar.py" in summary["description"]
    assert summary["status"] == "success"


def test_summarize_changes_falls_back_on_empty_llm_response(monkeypatch):
    from orchestrator.code_generator.nodes import summarize_changes as node_mod

    monkeypatch.setenv("GOOSE_MODEL", "openai/gpt-5-mini")
    with patch.object(node_mod.litellm, "completion", return_value=_mock_llm("")):
        result = node_mod.summarize_changes(_base_state())

    summary = result["code_generation_summary"]
    assert "Split code gen into two nodes" in summary["description"]


def test_summarize_changes_skips_llm_when_no_model_configured(monkeypatch):
    from orchestrator.code_generator.nodes import summarize_changes as node_mod

    monkeypatch.delenv("GOOSE_MODEL", raising=False)
    monkeypatch.delenv("SUMMARIZER_MODEL", raising=False)
    with patch.object(node_mod.litellm, "completion") as mock_completion:
        result = node_mod.summarize_changes(_base_state())

    assert not mock_completion.called
    summary = result["code_generation_summary"]
    assert summary["description"]  # fallback populated


def test_summarize_changes_skips_llm_on_failed_status(monkeypatch):
    """Failed runs use the fallback — no LLM call wasted describing "no commit"."""
    from orchestrator.code_generator.nodes import summarize_changes as node_mod

    monkeypatch.setenv("GOOSE_MODEL", "openai/gpt-5-mini")
    state = _base_state()
    state["raw_results"] = {
        "ticket_key": "AOS-291",
        "status": "failed",
        "branch": "",
        "commit_sha": "",
        "files_changed": [],
        "build": "fail",
        "tests": "skipped",
        "pr_url": "",
        "error": "commit failed",
    }
    with patch.object(node_mod.litellm, "completion") as mock_completion:
        result = node_mod.summarize_changes(state)

    assert not mock_completion.called
    summary = result["code_generation_summary"]
    assert summary["status"] == "failed"
    assert "commit failed" in summary["description"]


def test_summarize_changes_skips_llm_when_raw_results_empty(monkeypatch):
    """Guard: if load_raw_results already routed to persist_results but we
    still got here somehow, don't crash."""
    from orchestrator.code_generator.nodes import summarize_changes as node_mod

    monkeypatch.setenv("GOOSE_MODEL", "openai/gpt-5-mini")
    state = _base_state()
    state["raw_results"] = {}
    state["code_generation_summary"] = {"status": "failed"}
    with patch.object(node_mod.litellm, "completion") as mock_completion:
        result = node_mod.summarize_changes(state)

    assert not mock_completion.called
    assert result["code_generation_summary"] == {"status": "failed"}


def test_summarize_changes_is_idempotent_on_same_inputs(monkeypatch):
    """Re-running the summariser on identical inputs produces identical output.

    This underpins the retry story — a re-run of just this node after an
    LLM blip must produce a stable summary.
    """
    from orchestrator.code_generator.nodes import summarize_changes as node_mod

    monkeypatch.setenv("GOOSE_MODEL", "openai/gpt-5-mini")
    with patch.object(node_mod.litellm, "completion", return_value=_mock_llm("stable description")):
        first = node_mod.summarize_changes(_base_state())
        second = node_mod.summarize_changes(_base_state())

    assert first["code_generation_summary"] == second["code_generation_summary"]


def test_summarize_changes_includes_git_diff_in_prompt(monkeypatch, tmp_path):
    """When the working dir is a real git repo, the diff must be threaded
    through to the LLM prompt so the model describes what actually shipped."""
    from orchestrator.code_generator.nodes import summarize_changes as node_mod

    subprocess.run(["git", "init", "--quiet", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.co"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "seed"], cwd=tmp_path, check=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(["git", "checkout", "--quiet", "-b", "feat"], cwd=tmp_path, check=True)
    (tmp_path / "foo.py").write_text("def x():\n    return 42\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "change"], cwd=tmp_path, check=True)

    state = _base_state()
    state["working_dir"] = str(tmp_path)
    state["raw_results"]["diff_base_sha"] = base_sha

    monkeypatch.setenv("GOOSE_MODEL", "openai/gpt-5-mini")
    with patch.object(
        node_mod.litellm, "completion", return_value=_mock_llm("desc")
    ) as mock_completion:
        node_mod.summarize_changes(state)

    prompt = mock_completion.call_args.kwargs["messages"][1]["content"]
    assert "def x():" in prompt, "the actual diff must be included in the summariser prompt"
