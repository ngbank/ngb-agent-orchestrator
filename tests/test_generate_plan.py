"""Unit tests for graph/work_planner/nodes/generate_plan.py."""

import json
import os
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.work_planner.nodes.generate_plan import generate_plan

VALID_WORK_PLAN = {
    "schema_version": "1.0",
    "ticket_key": "AOS-51",
    "summary": "Implement plan generation",
    "approach": "Shell out to Goose plan recipe",
    "tasks": [{"id": 1, "description": "Do the thing", "files_likely_affected": ["a.py"]}],
    "concerns": [],
    "status": "pass",
}


def _make_run_result(returncode=0):
    mock = MagicMock()
    mock.returncode = returncode
    return mock


_PATCH_TEE = "orchestrator.work_planner.nodes.generate_plan.run_and_tee"
_PATCH_SESSION = "orchestrator.work_planner.nodes.generate_plan.goose_session"


@pytest.fixture(autouse=True)
def mock_goose_session():
    """Prevent goose_session from starting a real litellm proxy in tests."""

    @contextmanager
    def _noop(*args, **kwargs):
        yield {}

    with patch(_PATCH_SESSION, _noop):
        yield


@pytest.fixture
def log_tmp(tmp_path):
    """Compatibility fixture for tests that previously isolated stage logs."""
    yield tmp_path


@pytest.fixture
def write_workplan_to_output():
    """Side-effect for run_and_tee: writes a valid WorkPlan to output_path param."""

    def _side_effect(cmd, logger_name, **kwargs):
        assert logger_name == "subprocess.goose"
        params = [a for a in cmd if a.startswith("output_path=")]
        assert params, "output_path param not passed to goose"
        output_path = params[0].split("=", 1)[1]
        with open(output_path, "w") as f:
            json.dump(VALID_WORK_PLAN, f)
        return _make_run_result(returncode=0)

    return _side_effect


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_generate_plan_success(log_tmp, write_workplan_to_output):
    """Goose writes a valid WorkPlan → work_plan_data returned in state."""
    with patch(_PATCH_TEE) as mock_tee:
        mock_tee.side_effect = write_workplan_to_output
        result = generate_plan({"ticket_key": "AOS-51", "workflow_id": "test-wf"})

    assert "work_plan_data" in result
    assert result["work_plan_data"]["ticket_key"] == "AOS-51"
    assert "error" not in result


def test_generate_plan_passes_correct_params(log_tmp, write_workplan_to_output):
    """Goose is invoked with the correct recipe and params."""
    with patch(_PATCH_TEE) as mock_tee:
        mock_tee.side_effect = write_workplan_to_output
        generate_plan({"ticket_key": "AOS-51", "workflow_id": "test-wf"})

    cmd = mock_tee.call_args[0][0]
    assert "goose" in cmd
    assert any(a.endswith("orchestrator/work_planner/recipes/plan.yaml") for a in cmd)
    assert any(a == "ticket_key=AOS-51" for a in cmd)
    assert any(a.startswith("output_path=") for a in cmd)
    assert "cwd" not in mock_tee.call_args.kwargs


def test_generate_plan_cleans_up_temp_file(log_tmp, write_workplan_to_output):
    """Temp output file is deleted after successful run."""
    captured_path = {}

    def _side_effect(cmd, log_file, **kwargs):
        params = [a for a in cmd if a.startswith("output_path=")]
        output_path = params[0].split("=", 1)[1]
        captured_path["path"] = output_path
        with open(output_path, "w") as f:
            json.dump(VALID_WORK_PLAN, f)
        return _make_run_result(returncode=0)

    with patch(_PATCH_TEE) as mock_tee:
        mock_tee.side_effect = _side_effect
        generate_plan({"ticket_key": "AOS-51", "workflow_id": "test-wf"})

    assert not os.path.exists(captured_path["path"])


def test_generate_plan_uses_working_dir_as_cwd(log_tmp, write_workplan_to_output, tmp_path):
    """When working_dir is provided, goose runs with cwd=working_dir."""
    working_dir = tmp_path / "repo"
    working_dir.mkdir()

    with patch(_PATCH_TEE) as mock_tee:
        mock_tee.side_effect = write_workplan_to_output
        generate_plan(
            {
                "ticket_key": "AOS-51",
                "workflow_id": "test-wf",
                "working_dir": str(working_dir),
            }
        )

    assert mock_tee.call_args.kwargs.get("cwd") == str(working_dir)


def test_generate_plan_errors_for_missing_working_dir(log_tmp):
    """A missing working_dir should fail before invoking goose."""
    with patch(_PATCH_TEE) as mock_tee:
        result = generate_plan(
            {
                "ticket_key": "AOS-51",
                "workflow_id": "test-wf",
                "working_dir": "/tmp/does-not-exist-ngb-aos-122",
            }
        )

    assert "error" in result
    assert "Working directory does not exist" in result["error"]
    assert result.get("failed_node") == "generate_plan"
    mock_tee.assert_not_called()


# ---------------------------------------------------------------------------
# Goose failure paths
# ---------------------------------------------------------------------------


def test_generate_plan_goose_nonzero_exit(log_tmp):
    """Goose exits non-zero → error returned, no work_plan_data."""
    with patch(_PATCH_TEE) as mock_tee:
        mock_tee.return_value = _make_run_result(returncode=1)
        result = generate_plan({"ticket_key": "AOS-51", "workflow_id": "test-wf"})

    assert "error" in result
    assert "1" in result["error"]
    assert "work_plan_data" not in result


def test_generate_plan_output_file_missing(log_tmp):
    """Goose exits 0 but does not write the output file → error returned."""

    def _side_effect(cmd, log_file, **kwargs):
        # Delete the temp file to simulate Goose not writing it
        params = [a for a in cmd if a.startswith("output_path=")]
        output_path = params[0].split("=", 1)[1]
        os.unlink(output_path)
        return _make_run_result(returncode=0)

    with patch(_PATCH_TEE) as mock_tee:
        mock_tee.side_effect = _side_effect
        result = generate_plan({"ticket_key": "AOS-51", "workflow_id": "test-wf"})

    assert "error" in result
    assert "did not write" in result["error"]
    assert "work_plan_data" not in result


def test_generate_plan_invalid_json(log_tmp):
    """Goose writes a file that isn't valid JSON → error returned."""

    def _side_effect(cmd, log_file, **kwargs):
        params = [a for a in cmd if a.startswith("output_path=")]
        output_path = params[0].split("=", 1)[1]
        with open(output_path, "w") as f:
            f.write("this is not json {{")
        return _make_run_result(returncode=0)

    with patch(_PATCH_TEE) as mock_tee:
        mock_tee.side_effect = _side_effect
        result = generate_plan({"ticket_key": "AOS-51", "workflow_id": "test-wf"})

    assert "error" in result
    assert "invalid JSON" in result["error"]
    assert "work_plan_data" not in result


def test_generate_plan_empty_json(log_tmp):
    """Goose writes an empty JSON object → error returned."""

    def _side_effect(cmd, log_file, **kwargs):
        params = [a for a in cmd if a.startswith("output_path=")]
        output_path = params[0].split("=", 1)[1]
        with open(output_path, "w") as f:
            json.dump({}, f)
        return _make_run_result(returncode=0)

    with patch(_PATCH_TEE) as mock_tee:
        mock_tee.side_effect = _side_effect
        result = generate_plan({"ticket_key": "AOS-51", "workflow_id": "test-wf"})

    assert "error" in result
    assert "empty" in result["error"]
    assert "work_plan_data" not in result


# ---------------------------------------------------------------------------
# Recipe retry configuration
# ---------------------------------------------------------------------------


def test_plan_recipe_bounds_runaway_streams():
    """Plan recipe caps retries at 1 and requires non-empty output.

    A stalled LLM stream can burn ~10 min per attempt while leaving a 0-byte
    output file behind. To bound worst-case runtime:

    - `retry.max_retries` MUST be 1 (higher values silently triple the burn).
    - `retry.checks[0]` MUST use `test -s` (non-empty), not `test -f` (exists),
      so an aborted stream fails on the first check with a clear signal rather
      than masking as a JSON-decode error on check[1].

    The recipe is checked as text (rather than parsed YAML) to avoid adding a
    yaml dependency to the test module — the fields are simple scalars whose
    presence in the file is the meaningful assertion.
    """
    from pathlib import Path

    recipe_path = (
        Path(__file__).resolve().parent.parent
        / "orchestrator"
        / "work_planner"
        / "recipes"
        / "plan.yaml"
    )
    recipe_text = recipe_path.read_text()

    assert (
        "max_retries: 1" in recipe_text
    ), "plan recipe max_retries must be 1 to bound runaway-stream cost"
    assert (
        "max_retries: 3" not in recipe_text
    ), "plan recipe must not regress to max_retries: 3 (~30 min burn on stalls)"

    assert 'command: "test -s {{ output_path }}"' in recipe_text, (
        "first check must use `test -s` (non-empty), not `test -f` (exists), "
        "so a 0-byte output from an aborted stream fails clearly"
    )


# ---------------------------------------------------------------------------
# ACE injection point 1 (planner) — context_items_path plumbing
# See docs/ACE/08-ace-orchestrator-injection-points.md
# ---------------------------------------------------------------------------


_PATCH_RENDER = "orchestrator.work_planner.nodes.generate_plan.render_context_block"
_PATCH_SETTINGS = "orchestrator.work_planner.nodes.generate_plan.get_ace_settings"


def _make_settings(*, planner_active: bool, top_k: int = 10):
    settings = MagicMock()
    settings.is_planner_active.return_value = planner_active
    settings.top_k = top_k
    return settings


def test_generate_plan_omits_context_items_when_planner_disabled(log_tmp, write_workplan_to_output):
    """ACE off (default) → no retrieval, no context_items_path param."""
    with (
        patch(_PATCH_TEE) as mock_tee,
        patch(_PATCH_SETTINGS, return_value=_make_settings(planner_active=False)),
        patch(_PATCH_RENDER) as mock_render,
    ):
        mock_tee.side_effect = write_workplan_to_output
        generate_plan({"ticket_key": "AOS-51", "workflow_id": "test-wf"})

    mock_render.assert_not_called()
    cmd = mock_tee.call_args[0][0]
    assert not any(a.startswith("context_items_path=") for a in cmd)


def test_generate_plan_passes_context_items_path_when_planner_active(
    log_tmp, write_workplan_to_output
):
    """ACE planner on + non-empty block → temp file written, param passed."""
    rendered = "- [ESTABLISHED] Approach: use migrations for schema changes"
    captured = {}

    def _tee(cmd, logger_name, **kwargs):
        params = [a for a in cmd if a.startswith("context_items_path=")]
        if params:
            path = params[0].split("=", 1)[1]
            captured["path"] = path
            captured["contents"] = open(path).read()
        return write_workplan_to_output(cmd, logger_name, **kwargs)

    ticket = {
        "key": "AOS-51",
        "title": "Add migration for context items",
        "description": "Introduce a new SQLite migration.",
        "labels": [],
        "status": "To Do",
    }

    with (
        patch(_PATCH_TEE) as mock_tee,
        patch(_PATCH_SETTINGS, return_value=_make_settings(planner_active=True, top_k=7)),
        patch(_PATCH_RENDER, return_value=rendered) as mock_render,
    ):
        mock_tee.side_effect = _tee
        generate_plan({"ticket_key": "AOS-51", "workflow_id": "test-wf", "ticket": ticket})

    # render_context_block called with the ticket context + query text
    assert mock_render.call_count == 1
    ticket_ctx = mock_render.call_args.args[0]
    assert ticket_ctx.ticket_key == "AOS-51"
    assert ticket_ctx.ticket_summary == "Add migration for context items"
    assert ticket_ctx.project == "AOS"
    assert ticket_ctx.recipe_target == "planner"
    kwargs = mock_render.call_args.kwargs
    assert "Add migration for context items" in kwargs["query_text"]
    assert "Introduce a new SQLite migration." in kwargs["query_text"]
    assert kwargs["top_k"] == 7

    # Recipe param was rendered to a temp file that contains the block
    assert "path" in captured, "context_items_path param not passed to goose"
    assert captured["contents"] == rendered

    # Temp file cleaned up after the run
    assert not os.path.exists(captured["path"])


def test_generate_plan_skips_context_items_when_block_empty(log_tmp, write_workplan_to_output):
    """ACE planner on but no items retrieved → no param, no temp file."""
    with (
        patch(_PATCH_TEE) as mock_tee,
        patch(_PATCH_SETTINGS, return_value=_make_settings(planner_active=True)),
        patch(_PATCH_RENDER, return_value=""),
    ):
        mock_tee.side_effect = write_workplan_to_output
        generate_plan({"ticket_key": "AOS-51", "workflow_id": "test-wf", "ticket": None})

    cmd = mock_tee.call_args[0][0]
    assert not any(a.startswith("context_items_path=") for a in cmd)


def test_generate_plan_swallows_retrieval_errors(log_tmp, write_workplan_to_output):
    """Retrieval failure must not block plan generation — proceed without context."""
    with (
        patch(_PATCH_TEE) as mock_tee,
        patch(_PATCH_SETTINGS, return_value=_make_settings(planner_active=True)),
        patch(_PATCH_RENDER, side_effect=RuntimeError("store unavailable")),
    ):
        mock_tee.side_effect = write_workplan_to_output
        result = generate_plan({"ticket_key": "AOS-51", "workflow_id": "test-wf", "ticket": None})

    assert "work_plan_data" in result
    cmd = mock_tee.call_args[0][0]
    assert not any(a.startswith("context_items_path=") for a in cmd)


def test_plan_recipe_declares_context_items_path_parameter():
    """Recipe must declare context_items_path and render the guidance block."""
    from pathlib import Path

    recipe_path = (
        Path(__file__).resolve().parent.parent
        / "orchestrator"
        / "work_planner"
        / "recipes"
        / "plan.yaml"
    )
    recipe_text = recipe_path.read_text()

    assert (
        "key: context_items_path" in recipe_text
    ), "recipe must declare a context_items_path parameter for ACE injection point 1"
    assert (
        "{% if context_items_path %}" in recipe_text
    ), "recipe must gate the prior-workflow context block on context_items_path"
    assert "guidance, not constraints" in recipe_text, (
        "planner block must include the 'guidance, not constraints' framing "
        "(docs/ACE/08 injection point 1)"
    )
    # Placement contract: the guidance block must appear before Fetch Ticket.
    assert recipe_text.index("{% if context_items_path %}") < recipe_text.index(
        "acli jira workitem view"
    ), "context items block must render before the Fetch Ticket step"
