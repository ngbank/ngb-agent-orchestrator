"""Aggregate LLM token usage for a workflow stage from OTel spans."""

from __future__ import annotations

import json

from orchestrator.paths import workflow_logs_dir


def aggregate_token_usage(workflow_id: str, stage: str) -> dict:
    """Read otel.jsonl and aggregate ``llm.call`` span stats for a workflow+stage.

    Returns a dict with keys: stage, turns, prompt_tokens, completion_tokens,
    total_tokens, stop_reasons.
    """
    jsonl_path = workflow_logs_dir(workflow_id, ensure_dir=False) / "otel.jsonl"

    turns = 0
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    stop_reasons: list[str] = []

    try:
        with jsonl_path.open("r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("name") != "llm.call":
                    continue
                attributes = entry.get("attributes") or {}
                if attributes.get("workflow.stage") != stage:
                    continue
                status = entry.get("status") or {}
                if status.get("status_code") != "OK":
                    continue
                turns += 1
                prompt_tokens += attributes.get("llm.input_tokens", 0) or 0
                completion_tokens += attributes.get("llm.output_tokens", 0) or 0
                total_tokens += attributes.get("llm.total_tokens", 0) or 0
                reason = attributes.get("llm.finish_reason")
                if reason:
                    stop_reasons.append(reason)
    except FileNotFoundError:
        pass

    return {
        "stage": stage,
        "turns": turns,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "stop_reasons": stop_reasons,
    }
