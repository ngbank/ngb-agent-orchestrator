"""Unit tests for litellm_callbacks.aggregate_token_usage."""

import json
import os
import tempfile

import pytest

from graph.litellm_callbacks import aggregate_token_usage


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
