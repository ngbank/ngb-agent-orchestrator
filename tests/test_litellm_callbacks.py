"""Unit tests for litellm_callbacks.aggregate_token_usage and TokenUsageLogger."""

import asyncio
import json
import os
import tempfile
from datetime import datetime, timezone

import pytest

from graph.litellm_callbacks import TokenUsageLogger, aggregate_token_usage


@pytest.fixture
def jsonl_dir(monkeypatch):
    """Create a temp dir that acts as LOGS_DIR and yield it."""
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("LOGS_DIR", tmp)
        yield tmp


def _write_entries(logs_dir: str, workflow_id: str, entries: list[dict]) -> None:
    wf_dir = os.path.join(logs_dir, workflow_id)
    os.makedirs(wf_dir, exist_ok=True)
    path = os.path.join(wf_dir, "llm_token_usage.jsonl")
    with open(path, "w") as fp:
        for entry in entries:
            fp.write(json.dumps(entry) + "\n")


def test_aggregate_basic(jsonl_dir):
    wf_id = "wf-001"
    _write_entries(
        jsonl_dir,
        wf_id,
        [
            {
                "workflow_id": wf_id,
                "stage": "plan",
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "stop_reason": "stop",
            },
            {
                "workflow_id": wf_id,
                "stage": "plan",
                "prompt_tokens": 200,
                "completion_tokens": 80,
                "total_tokens": 280,
                "stop_reason": "stop",
            },
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
    _write_entries(
        jsonl_dir,
        wf_id,
        [
            {
                "workflow_id": wf_id,
                "stage": "plan",
                "prompt_tokens": 100,
                "completion_tokens": 40,
                "total_tokens": 140,
                "stop_reason": "stop",
            },
            {
                "workflow_id": wf_id,
                "stage": "execute",
                "prompt_tokens": 500,
                "completion_tokens": 200,
                "total_tokens": 700,
                "stop_reason": "max_tokens",
            },
        ],
    )

    plan_result = aggregate_token_usage(wf_id, "plan")
    exec_result = aggregate_token_usage(wf_id, "execute")

    assert plan_result["turns"] == 1
    assert plan_result["total_tokens"] == 140
    assert exec_result["turns"] == 1
    assert exec_result["total_tokens"] == 700
    assert exec_result["stop_reasons"] == ["max_tokens"]


def test_aggregate_filters_by_workflow_id(jsonl_dir):
    wf_a, wf_b = "wf-aaa", "wf-bbb"
    # Write entries for wf_a only
    _write_entries(
        jsonl_dir,
        wf_a,
        [
            {
                "workflow_id": wf_a,
                "stage": "plan",
                "prompt_tokens": 50,
                "completion_tokens": 20,
                "total_tokens": 70,
                "stop_reason": "stop",
            },
            # Entry with a different workflow_id in the same file (shouldn't happen
            # in practice but we guard against it)
            {
                "workflow_id": wf_b,
                "stage": "plan",
                "prompt_tokens": 9999,
                "completion_tokens": 9999,
                "total_tokens": 9999,
                "stop_reason": "stop",
            },
        ],
    )

    result = aggregate_token_usage(wf_a, "plan")

    assert result["turns"] == 1
    assert result["total_tokens"] == 70


def test_aggregate_missing_file_returns_zeros(jsonl_dir):
    result = aggregate_token_usage("nonexistent-wf", "plan")

    assert result["stage"] == "plan"
    assert result["turns"] == 0
    assert result["prompt_tokens"] == 0
    assert result["completion_tokens"] == 0
    assert result["total_tokens"] == 0
    assert result["stop_reasons"] == []


def test_aggregate_skips_malformed_lines(jsonl_dir):
    wf_id = "wf-003"
    wf_dir = os.path.join(jsonl_dir, wf_id)
    os.makedirs(wf_dir, exist_ok=True)
    path = os.path.join(wf_dir, "llm_token_usage.jsonl")
    with open(path, "w") as fp:
        fp.write("not-json\n")
        fp.write(
            json.dumps(
                {
                    "workflow_id": wf_id,
                    "stage": "plan",
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "stop_reason": "stop",
                }
            )
            + "\n"
        )

    result = aggregate_token_usage(wf_id, "plan")

    assert result["turns"] == 1
    assert result["total_tokens"] == 15


def test_aggregate_null_stop_reason_excluded(jsonl_dir):
    wf_id = "wf-004"
    _write_entries(
        jsonl_dir,
        wf_id,
        [
            {
                "workflow_id": wf_id,
                "stage": "execute",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "stop_reason": None,
            },
        ],
    )

    result = aggregate_token_usage(wf_id, "execute")

    assert result["turns"] == 1
    assert result["stop_reasons"] == []


# ---------------------------------------------------------------------------
# TokenUsageLogger.async_log_failure_event
# ---------------------------------------------------------------------------


@pytest.fixture
def failure_logger_env(monkeypatch):
    """Set env vars and LOGS_DIR so the logger writes to a temp dir."""
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("LOGS_DIR", tmp)
        monkeypatch.setenv("NGB_WORKFLOW_ID", "wf-fail-001")
        monkeypatch.setenv("NGB_WORKFLOW_STAGE", "execute")
        yield tmp


def test_failure_event_writes_to_jsonl(failure_logger_env):
    """async_log_failure_event appends one entry to llm_failures.jsonl."""
    logger = TokenUsageLogger()
    exc = RuntimeError("Stream decode error: missing field `error`")
    kwargs = {
        "model": "azure/gpt-5.4",
        "litellm_call_id": "call-abc123",
        "exception": exc,
        "traceback_exception": "Traceback ...",
        "original_response": '{"type":"response.failed","response":{"status":"failed","output":[]}}',
        "additional_args": {"api_base": "https://example.azure.com"},
    }

    asyncio.run(
        logger.async_log_failure_event(
            kwargs, None, datetime.now(timezone.utc), datetime.now(timezone.utc)
        )
    )

    failures_path = os.path.join(failure_logger_env, "wf-fail-001", "llm_failures.jsonl")
    assert os.path.exists(failures_path), "llm_failures.jsonl was not created"

    with open(failures_path) as fp:
        entry = json.loads(fp.readline())

    assert entry["workflow_id"] == "wf-fail-001"
    assert entry["stage"] == "execute"
    assert entry["model"] == "azure/gpt-5.4"
    assert entry["request_id"] == "call-abc123"
    assert entry["exception_type"] == "RuntimeError"
    assert "missing field" in entry["exception_message"]
    assert entry["traceback"] == "Traceback ..."
    assert "response.failed" in entry["original_response"]


def test_failure_event_handles_missing_fields(failure_logger_env):
    """async_log_failure_event handles kwargs with no exception or response."""
    logger = TokenUsageLogger()

    asyncio.run(
        logger.async_log_failure_event(
            {}, None, datetime.now(timezone.utc), datetime.now(timezone.utc)
        )
    )

    failures_path = os.path.join(failure_logger_env, "wf-fail-001", "llm_failures.jsonl")
    assert os.path.exists(failures_path)
    with open(failures_path) as fp:
        entry = json.loads(fp.readline())
    assert entry["exception_type"] is None
    assert entry["original_response"] is None
