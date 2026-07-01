"""Unit tests for litellm_callbacks.aggregate_token_usage."""

import json
import os
import tempfile

import pytest

from orchestrator.litellm_callbacks import aggregate_token_usage


@pytest.fixture
def jsonl_dir(monkeypatch):
    """Create a temp dir that acts as LOGS_DIR and yield it."""
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("LOGS_DIR", tmp)
        yield tmp


def _span(
    *,
    name="llm.call",
    stage="plan",
    input_tokens=0,
    output_tokens=0,
    total_tokens=0,
    finish_reason=None,
    status_code="OK",
) -> dict:
    attributes = {"workflow.stage": stage}
    if input_tokens:
        attributes["llm.input_tokens"] = input_tokens
    if output_tokens:
        attributes["llm.output_tokens"] = output_tokens
    if total_tokens:
        attributes["llm.total_tokens"] = total_tokens
    if finish_reason is not None:
        attributes["llm.finish_reason"] = finish_reason
    return {
        "name": name,
        "attributes": attributes,
        "status": {"status_code": status_code, "description": None},
    }


def _write_spans(logs_dir: str, workflow_id: str, spans: list[dict]) -> None:
    wf_dir = os.path.join(logs_dir, workflow_id)
    os.makedirs(wf_dir, exist_ok=True)
    path = os.path.join(wf_dir, "otel.jsonl")
    with open(path, "w") as fp:
        for span in spans:
            fp.write(json.dumps(span) + "\n")


def test_aggregate_basic(jsonl_dir):
    wf_id = "wf-001"
    _write_spans(
        jsonl_dir,
        wf_id,
        [
            _span(
                stage="plan",
                input_tokens=100,
                output_tokens=50,
                total_tokens=150,
                finish_reason="stop",
            ),
            _span(
                stage="plan",
                input_tokens=200,
                output_tokens=80,
                total_tokens=280,
                finish_reason="stop",
            ),
        ],
    )

    result = aggregate_token_usage(wf_id, "plan")

    assert result["stage"] == "plan"
    assert result["turns"] == 2
    assert result["prompt_tokens"] == 300
    assert result["completion_tokens"] == 130
    assert result["total_tokens"] == 430
    assert result["stop_reasons"] == ["stop", "stop"]


def test_aggregate_filters_by_stage(jsonl_dir):
    wf_id = "wf-002"
    _write_spans(
        jsonl_dir,
        wf_id,
        [
            _span(stage="plan", input_tokens=100, output_tokens=40, total_tokens=140),
            _span(
                stage="execute",
                input_tokens=500,
                output_tokens=200,
                total_tokens=700,
                finish_reason="max_tokens",
            ),
        ],
    )

    plan_result = aggregate_token_usage(wf_id, "plan")
    exec_result = aggregate_token_usage(wf_id, "execute")

    assert plan_result["turns"] == 1
    assert plan_result["total_tokens"] == 140
    assert exec_result["turns"] == 1
    assert exec_result["total_tokens"] == 700
    assert exec_result["stop_reasons"] == ["max_tokens"]


def test_aggregate_ignores_non_llm_call_spans(jsonl_dir):
    wf_id = "wf-003"
    _write_spans(
        jsonl_dir,
        wf_id,
        [
            _span(name="graph.node.work_planner", stage="plan"),
            _span(stage="plan", total_tokens=70, finish_reason="stop"),
        ],
    )

    result = aggregate_token_usage(wf_id, "plan")

    assert result["turns"] == 1
    assert result["total_tokens"] == 70


def test_aggregate_ignores_failed_spans(jsonl_dir):
    wf_id = "wf-004"
    _write_spans(
        jsonl_dir,
        wf_id,
        [
            _span(stage="execute", status_code="ERROR"),
            _span(stage="execute", total_tokens=15, finish_reason="stop"),
        ],
    )

    result = aggregate_token_usage(wf_id, "execute")

    assert result["turns"] == 1
    assert result["total_tokens"] == 15


def test_aggregate_missing_file_returns_zeros(jsonl_dir):
    result = aggregate_token_usage("nonexistent-wf", "plan")

    assert result["stage"] == "plan"
    assert result["turns"] == 0
    assert result["prompt_tokens"] == 0
    assert result["completion_tokens"] == 0
    assert result["total_tokens"] == 0
    assert result["stop_reasons"] == []


def test_aggregate_skips_malformed_lines(jsonl_dir):
    wf_id = "wf-005"
    wf_dir = os.path.join(jsonl_dir, wf_id)
    os.makedirs(wf_dir, exist_ok=True)
    path = os.path.join(wf_dir, "otel.jsonl")
    with open(path, "w") as fp:
        fp.write("not-json\n")
        fp.write(json.dumps(_span(stage="plan", total_tokens=15, finish_reason="stop")) + "\n")

    result = aggregate_token_usage(wf_id, "plan")

    assert result["turns"] == 1
    assert result["total_tokens"] == 15


def test_aggregate_null_finish_reason_excluded(jsonl_dir):
    wf_id = "wf-006"
    _write_spans(
        jsonl_dir,
        wf_id,
        [_span(stage="execute", total_tokens=15)],
    )

    result = aggregate_token_usage(wf_id, "execute")

    assert result["turns"] == 1
    assert result["stop_reasons"] == []


def test_aggregate_does_not_require_legacy_jsonl(jsonl_dir):
    """Migration: aggregation works from otel.jsonl with no llm_token_usage.jsonl present."""
    wf_id = "wf-007"
    _write_spans(
        jsonl_dir,
        wf_id,
        [_span(stage="plan", total_tokens=42, finish_reason="stop")],
    )

    legacy_path = os.path.join(jsonl_dir, wf_id, "llm_token_usage.jsonl")
    assert not os.path.exists(legacy_path)

    result = aggregate_token_usage(wf_id, "plan")

    assert result["turns"] == 1
    assert result["total_tokens"] == 42
