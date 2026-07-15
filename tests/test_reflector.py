"""Unit tests for ace.pipeline.reflector.

The LLM is mocked at ``ace.pipeline.reflector.litellm.completion`` — no
network calls, no API keys required.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from ace.models import CandidateItem
from ace.pipeline.reflector import (
    ReflectorError,
    comment_recall,
    reflect,
    split_comment_units,
)
from ace.pipeline.trace_reader import TraceBundle

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _bundle(**overrides: Any) -> TraceBundle:
    """Minimal TraceBundle for reflector tests."""
    defaults = dict(
        workflow_id="wf-abc-123",
        ticket_key="AOS-1",
        status="completed",
        created_at="2026-01-01T00:00:00",
        work_plan={"status": "concerns", "concerns": ["schema change needs migration"]},
        code_generation_summary={"status": "success", "branch": "feature/AOS-1+x"},
        clarification_history=[{"round": 1, "concerns": ["x"], "answers": ["y"]}],
        pr_comments=[],
        rejection_reason=None,
    )
    defaults.update(overrides)
    return TraceBundle(**defaults)  # type: ignore[arg-type]


def _mock_response(content: str) -> MagicMock:
    """Build a minimal litellm response mock with the given message content."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def _valid_candidate_json(**overrides: Any) -> dict:
    base = {
        "pattern_type": "concern",
        "scope": "codebase_wide",
        "scope_value": None,
        "description": "Schema-changing tickets should include a migration file.",
        "evidence": [{"signal_source": "plan_concern", "detail": "schema change needs migration"}],
        "initial_confidence": 0.7,
        "suggested_tier": "PATTERN",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _set_model(monkeypatch):
    monkeypatch.setenv("ACE_REFLECTOR_MODEL", "openai/gpt-4o")
    monkeypatch.delenv("GOOSE_MODEL", raising=False)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_reflect_returns_list_of_candidates():
    response_body = json.dumps({"candidates": [_valid_candidate_json()]})
    with patch(
        "ace.pipeline.reflector.litellm.completion",
        return_value=_mock_response(response_body),
    ):
        result = reflect(_bundle())

    assert len(result) == 1
    assert isinstance(result[0], CandidateItem)
    assert result[0].pattern_type == "concern"
    assert result[0].scope == "codebase_wide"
    assert result[0].scope_value is None
    assert result[0].initial_confidence == 0.7
    assert result[0].suggested_tier == "PATTERN"


def test_reflect_returns_empty_list_when_no_signal():
    """An empty candidates list is a valid, non-error outcome."""
    with patch(
        "ace.pipeline.reflector.litellm.completion",
        return_value=_mock_response(json.dumps({"candidates": []})),
    ):
        result = reflect(_bundle())

    assert result == []


def test_reflect_injects_workflow_id_into_evidence():
    """Every evidence entry must carry the workflow_id for provenance."""
    candidate = _valid_candidate_json(
        evidence=[{"signal_source": "pr_comment", "detail": "rename x to context"}]
    )
    with patch(
        "ace.pipeline.reflector.litellm.completion",
        return_value=_mock_response(json.dumps({"candidates": [candidate]})),
    ):
        result = reflect(_bundle(workflow_id="wf-xyz-999"))

    assert result[0].evidence[0]["workflow_id"] == "wf-xyz-999"
    assert result[0].evidence[0]["signal_source"] == "pr_comment"


def test_reflect_handles_multiple_candidates():
    candidates = [
        _valid_candidate_json(description="Rule A"),
        _valid_candidate_json(
            pattern_type="test_coverage",
            description="Rule B",
            initial_confidence=0.85,
            suggested_tier="PATTERN",
        ),
    ]
    with patch(
        "ace.pipeline.reflector.litellm.completion",
        return_value=_mock_response(json.dumps({"candidates": candidates})),
    ):
        result = reflect(_bundle())

    assert len(result) == 2
    assert result[0].description == "Rule A"
    assert result[1].description == "Rule B"


def test_reflect_truncates_above_max_candidates():
    """More than 5 candidates gets truncated defensively, not rejected."""
    candidates = [_valid_candidate_json(description=f"Rule {i}") for i in range(8)]
    with patch(
        "ace.pipeline.reflector.litellm.completion",
        return_value=_mock_response(json.dumps({"candidates": candidates})),
    ):
        result = reflect(_bundle())

    assert len(result) == 5


# ---------------------------------------------------------------------------
# Defensive parsing (markdown fences, extra prose)
# ---------------------------------------------------------------------------


def test_reflect_parses_json_wrapped_in_markdown_fence():
    payload = json.dumps({"candidates": [_valid_candidate_json()]})
    fenced = f"```json\n{payload}\n```"
    with patch(
        "ace.pipeline.reflector.litellm.completion",
        return_value=_mock_response(fenced),
    ):
        result = reflect(_bundle())

    assert len(result) == 1


def test_reflect_extracts_json_from_surrounding_prose():
    payload = json.dumps({"candidates": [_valid_candidate_json()]})
    noisy = f"Here is my answer:\n{payload}\nHope this helps!"
    with patch(
        "ace.pipeline.reflector.litellm.completion",
        return_value=_mock_response(noisy),
    ):
        result = reflect(_bundle())

    assert len(result) == 1


# ---------------------------------------------------------------------------
# Retry on parse failure
# ---------------------------------------------------------------------------


def test_reflect_retries_once_on_parse_failure():
    """Malformed JSON on attempt 1, valid JSON on attempt 2 → success."""
    good = json.dumps({"candidates": [_valid_candidate_json()]})
    with patch(
        "ace.pipeline.reflector.litellm.completion",
        side_effect=[
            _mock_response("not JSON at all"),
            _mock_response(good),
        ],
    ) as mock_call:
        result = reflect(_bundle())

    assert len(result) == 1
    assert mock_call.call_count == 2


def test_reflect_raises_after_two_failed_parses():
    with patch(
        "ace.pipeline.reflector.litellm.completion",
        side_effect=[
            _mock_response("not JSON"),
            _mock_response("still not JSON"),
        ],
    ) as mock_call:
        with pytest.raises(ReflectorError, match="after 2 attempts"):
            reflect(_bundle())

    assert mock_call.call_count == 2


def test_reflect_does_not_retry_api_errors():
    """Auth/quota/network failures should not be retried by the reflector."""
    with patch(
        "ace.pipeline.reflector.litellm.completion",
        side_effect=RuntimeError("connection refused"),
    ) as mock_call:
        with pytest.raises(ReflectorError, match="connection refused"):
            reflect(_bundle())

    assert mock_call.call_count == 1


# ---------------------------------------------------------------------------
# Validation — bad pattern_type / scope / confidence / etc.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "override",
    [
        {"pattern_type": "not_a_type"},
        {"scope": "not_a_scope"},
        {"initial_confidence": 0.3},  # below floor
        {"initial_confidence": 1.5},  # above ceiling
        {"initial_confidence": "high"},  # wrong type
        {"description": ""},  # empty
        {"description": None},
        {"suggested_tier": "GOLDEN"},  # not in allowed set
    ],
)
def test_reflect_rejects_invalid_candidate_fields(override: dict):
    """Any structural violation on both attempts → ReflectorError."""
    bad = _valid_candidate_json(**override)
    body = json.dumps({"candidates": [bad]})
    with patch(
        "ace.pipeline.reflector.litellm.completion",
        return_value=_mock_response(body),
    ):
        with pytest.raises(ReflectorError):
            reflect(_bundle())


def test_reflect_rejects_missing_candidates_key():
    body = json.dumps({"items": []})  # wrong key
    with patch(
        "ace.pipeline.reflector.litellm.completion",
        return_value=_mock_response(body),
    ):
        with pytest.raises(ReflectorError):
            reflect(_bundle())


def test_reflect_rejects_task_type_scope_without_scope_value():
    bad = _valid_candidate_json(scope="task_type", scope_value=None)
    body = json.dumps({"candidates": [bad]})
    with patch(
        "ace.pipeline.reflector.litellm.completion",
        return_value=_mock_response(body),
    ):
        with pytest.raises(ReflectorError):
            reflect(_bundle())


def test_reflect_rejects_file_pattern_scope_without_scope_value():
    bad = _valid_candidate_json(scope="file_pattern", scope_value="")
    body = json.dumps({"candidates": [bad]})
    with patch(
        "ace.pipeline.reflector.litellm.completion",
        return_value=_mock_response(body),
    ):
        with pytest.raises(ReflectorError):
            reflect(_bundle())


def test_reflect_normalises_codebase_wide_scope_value_to_none():
    """codebase_wide + non-null scope_value → scope_value coerced to None."""
    raw = _valid_candidate_json(scope="codebase_wide", scope_value="ignored")
    body = json.dumps({"candidates": [raw]})
    with patch(
        "ace.pipeline.reflector.litellm.completion",
        return_value=_mock_response(body),
    ):
        result = reflect(_bundle())

    assert result[0].scope_value is None


# ---------------------------------------------------------------------------
# Applicability dimensions
# ---------------------------------------------------------------------------


def test_reflect_defaults_applicability_dimensions_to_none():
    """Candidates without applicability keys parse as None on all three axes."""
    body = json.dumps({"candidates": [_valid_candidate_json()]})
    with patch(
        "ace.pipeline.reflector.litellm.completion",
        return_value=_mock_response(body),
    ):
        result = reflect(_bundle())

    assert result[0].project is None
    assert result[0].repo is None
    assert result[0].platform is None


def test_reflect_carries_applicability_dimensions_when_present():
    raw = _valid_candidate_json(
        project="AOS",
        repo="ngb-agent-orchestrator",
        platform="python",
    )
    body = json.dumps({"candidates": [raw]})
    with patch(
        "ace.pipeline.reflector.litellm.completion",
        return_value=_mock_response(body),
    ):
        result = reflect(_bundle())

    assert result[0].project == "AOS"
    assert result[0].repo == "ngb-agent-orchestrator"
    assert result[0].platform == "python"


def test_reflect_normalises_blank_applicability_to_none():
    """Empty / whitespace-only strings collapse to None so retrieval sees uniform nulls."""
    raw = _valid_candidate_json(project="", repo="   ", platform=None)
    body = json.dumps({"candidates": [raw]})
    with patch(
        "ace.pipeline.reflector.litellm.completion",
        return_value=_mock_response(body),
    ):
        result = reflect(_bundle())

    assert result[0].project is None
    assert result[0].repo is None
    assert result[0].platform is None


@pytest.mark.parametrize(
    "field",
    ["project", "repo", "platform"],
)
def test_reflect_rejects_non_string_applicability_fields(field: str):
    raw = _valid_candidate_json(**{field: 123})
    body = json.dumps({"candidates": [raw]})
    with patch(
        "ace.pipeline.reflector.litellm.completion",
        return_value=_mock_response(body),
    ):
        with pytest.raises(ReflectorError):
            reflect(_bundle())


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------


def test_reflect_uses_ace_reflector_model_when_set(monkeypatch):
    monkeypatch.setenv("ACE_REFLECTOR_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("GOOSE_MODEL", "openai/gpt-4o")
    body = json.dumps({"candidates": []})
    with patch(
        "ace.pipeline.reflector.litellm.completion",
        return_value=_mock_response(body),
    ) as mock_call:
        reflect(_bundle())

    kwargs = mock_call.call_args.kwargs
    assert kwargs["model"] == "openai/gpt-4o-mini"


def test_reflect_falls_back_to_goose_model(monkeypatch):
    monkeypatch.delenv("ACE_REFLECTOR_MODEL", raising=False)
    monkeypatch.setenv("GOOSE_MODEL", "openai/gpt-4o")
    body = json.dumps({"candidates": []})
    with patch(
        "ace.pipeline.reflector.litellm.completion",
        return_value=_mock_response(body),
    ) as mock_call:
        reflect(_bundle())

    kwargs = mock_call.call_args.kwargs
    assert kwargs["model"] == "openai/gpt-4o"


def test_reflect_raises_when_no_model_configured(monkeypatch):
    monkeypatch.delenv("ACE_REFLECTOR_MODEL", raising=False)
    monkeypatch.delenv("GOOSE_MODEL", raising=False)
    with pytest.raises(ReflectorError, match="ACE_REFLECTOR_MODEL"):
        reflect(_bundle())


# ---------------------------------------------------------------------------
# LLM invocation shape (system+user, json_object)
# ---------------------------------------------------------------------------


def test_reflect_calls_llm_with_system_and_user_messages():
    body = json.dumps({"candidates": []})
    with patch(
        "ace.pipeline.reflector.litellm.completion",
        return_value=_mock_response(body),
    ) as mock_call:
        reflect(_bundle(ticket_key="AOS-42", workflow_id="wf-42"))

    kwargs = mock_call.call_args.kwargs
    messages = kwargs["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "Reflector" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    # The user message must include the trace payload as JSON so the LLM
    # reads structured keys, not paraphrased prose.
    assert "AOS-42" in messages[1]["content"]
    assert "wf-42" in messages[1]["content"]
    # temperature is intentionally omitted so GPT-5-family models
    # (which only accept the default value) work through the same code path.
    assert "temperature" not in kwargs
    assert kwargs["response_format"] == {"type": "json_object"}


def test_reflect_empty_llm_content_triggers_retry():
    """Empty content is a parse error → gets one retry."""
    good = json.dumps({"candidates": []})
    with patch(
        "ace.pipeline.reflector.litellm.completion",
        side_effect=[_mock_response(""), _mock_response(good)],
    ) as mock_call:
        result = reflect(_bundle())

    assert result == []
    assert mock_call.call_count == 2


# ---------------------------------------------------------------------------
# Comment units & recall (AOS-272)
# ---------------------------------------------------------------------------


def _pr_round(round_n: int, comments: str) -> dict:
    return {
        "round": round_n,
        "comments": comments,
        "actor": "reviewer",
        "timestamp": f"2026-01-0{round_n}T00:00:00+00:00",
    }


def test_split_comment_units_numbers_paragraphs_across_rounds():
    rounds = [
        _pr_round(1, "Please remove the .venv symlink.\n\nAlso use ctx.exit()."),
        _pr_round(2, "The .venv symlink is back again."),
    ]

    units = split_comment_units(rounds)

    assert [u["id"] for u in units] == ["pr_comment_1", "pr_comment_2", "pr_comment_3"]
    assert units[0]["text"] == "Please remove the .venv symlink."
    assert units[1]["text"] == "Also use ctx.exit()."
    assert units[2]["round"] == 2
    assert units[2]["actor"] == "reviewer"


def test_split_comment_units_skips_blank_and_non_string_comments():
    rounds = [
        _pr_round(1, "  \n\n   "),
        {"round": 2, "comments": None, "actor": "reviewer", "timestamp": "t"},
        "not-a-dict",
        _pr_round(3, "One real critique."),
    ]

    units = split_comment_units(rounds)

    assert len(units) == 1
    assert units[0]["id"] == "pr_comment_1"
    assert units[0]["text"] == "One real critique."


def test_split_comment_units_empty_input():
    assert split_comment_units([]) == []


def _candidate_with_evidence(*signal_sources: str) -> CandidateItem:
    return CandidateItem(
        pattern_type="implementation",
        scope="codebase_wide",
        description="Never commit virtualenv directories or symlinks to them.",
        initial_confidence=0.65,
        evidence=[{"signal_source": s, "detail": "quote"} for s in signal_sources],
    )


def test_comment_recall_counts_distinct_cited_units():
    bundle = _bundle(
        pr_comments=[_pr_round(1, "Critique A.\n\nCritique B.\n\nCritique C.")],
    )
    candidates = [
        _candidate_with_evidence("pr_comment_1", "pr_comment_3"),
        _candidate_with_evidence("pr_comment_1"),  # duplicate citation
    ]

    assert comment_recall(bundle, candidates) == (3, 2)


def test_comment_recall_ignores_legacy_and_out_of_range_sources():
    bundle = _bundle(pr_comments=[_pr_round(1, "Only one critique.")])
    candidates = [
        _candidate_with_evidence("pr_comment", "pr_comment_9", "plan_concern"),
    ]

    assert comment_recall(bundle, candidates) == (1, 0)


def test_comment_recall_zero_units_without_pr_comments():
    assert comment_recall(_bundle(pr_comments=[]), []) == (0, 0)


def test_reflect_user_message_contains_numbered_comment_units():
    body = json.dumps({"candidates": []})
    bundle = _bundle(
        pr_comments=[_pr_round(1, "Remove the .venv symlink.\n\nUse ctx.exit().")],
    )
    with patch(
        "ace.pipeline.reflector.litellm.completion",
        return_value=_mock_response(body),
    ) as mock_call:
        reflect(bundle)

    user_message = mock_call.call_args.kwargs["messages"][1]["content"]
    assert "pr_comment_1" in user_message
    assert "pr_comment_2" in user_message
    assert "Remove the .venv symlink." in user_message
