"""LiteLLM proxy callbacks for token usage tracking."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

from orchestrator.log_paths import workflow_logs_dir

_WRITE_LOCK = Lock()


def _logs_dir() -> Path:
    workflow_id = os.getenv("NGB_WORKFLOW_ID", "unknown")
    return workflow_logs_dir(workflow_id)


def _token_usage_path() -> Path:
    return _logs_dir() / "llm_token_usage.jsonl"


def _to_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            data = model_dump()
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    dict_value = getattr(value, "dict", None)
    if callable(dict_value):
        try:
            data = dict_value()
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    return {}


def _coalesce_int(*values: Any) -> int:
    for value in values:
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _llm_failures_path() -> Path:
    return _logs_dir() / "llm_failures.jsonl"


class TokenUsageLogger(CustomLogger):
    """Append one JSON line with usage stats for each completed LLM call."""

    async def async_log_failure_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        exception = kwargs.get("exception")
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "workflow_id": os.getenv("NGB_WORKFLOW_ID", ""),
            "stage": os.getenv("NGB_WORKFLOW_STAGE", ""),
            "model": kwargs.get("model"),
            "request_id": kwargs.get("litellm_call_id"),
            "exception_type": type(exception).__name__ if exception else None,
            "exception_message": str(exception) if exception else None,
            "traceback": kwargs.get("traceback_exception"),
            "original_response": kwargs.get("original_response"),
            "additional_args": kwargs.get("additional_args"),
        }
        line = json.dumps(entry, default=str, separators=(",", ":"), ensure_ascii=True)
        with _WRITE_LOCK:
            with _llm_failures_path().open("a", encoding="utf-8") as fp:
                fp.write(line + "\n")

    async def async_log_success_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        response = _to_dict(response_obj)
        usage = _to_dict(response.get("usage"))
        if not usage and isinstance(response_obj, dict):
            usage = _to_dict(response_obj.get("usage"))

        # Responses API can expose input/output token keys, while chat completions
        # usually uses prompt/completion token keys.
        prompt_tokens = _coalesce_int(
            usage.get("prompt_tokens"),
            usage.get("input_tokens"),
        )
        completion_tokens = _coalesce_int(
            usage.get("completion_tokens"),
            usage.get("output_tokens"),
        )
        total_tokens = _coalesce_int(
            usage.get("total_tokens"),
            prompt_tokens + completion_tokens,
        )

        # Responses API stop reason lives in output items or status, not choices.
        output_items = response.get("output") or []
        responses_api_stop = None
        if output_items:
            last_item = output_items[-1] if isinstance(output_items, list) else {}
            responses_api_stop = last_item.get("stop_reason") or last_item.get("type")
        stop_reason = (
            response.get("choices", [{}])[0].get("finish_reason")
            or response.get("stop_reason")
            or response.get("finish_reason")
            or responses_api_stop
            or response.get("status")
        )

        # Compact request summary for post-hoc debugging (avoids storing full payload).
        req_summary: dict[str, Any] = {}
        additional_args = kwargs.get("additional_args") or {}
        complete_input = additional_args.get("complete_input_dict") or {}
        input_messages = complete_input.get("input") or []
        if input_messages:
            last_fc = next(
                (
                    m.get("name")
                    for m in reversed(input_messages)
                    if m.get("type") == "function_call"
                ),
                None,
            )
            req_summary = {
                "num_messages": len(input_messages),
                "last_tool_call": last_fc,
                "reasoning": complete_input.get("reasoning"),
                "truncation": complete_input.get("truncation"),
                "max_output_tokens": complete_input.get("max_output_tokens"),
            }

        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "workflow_id": os.getenv("NGB_WORKFLOW_ID", ""),
            "stage": os.getenv("NGB_WORKFLOW_STAGE", ""),
            "model": kwargs.get("model"),
            "request_id": response.get("id") or kwargs.get("litellm_call_id"),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "stop_reason": stop_reason,
            "usage": usage,
            "req": req_summary,
        }

        line = json.dumps(entry, separators=(",", ":"), ensure_ascii=True)
        with _WRITE_LOCK:
            with _token_usage_path().open("a", encoding="utf-8") as fp:
                fp.write(line + "\n")


proxy_handler_instance = TokenUsageLogger()


def aggregate_token_usage(workflow_id: str, stage: str) -> dict:
    """Read llm_token_usage.jsonl and aggregate stats for a specific workflow+stage.

    Returns a dict with keys: stage, turns, prompt_tokens, completion_tokens,
    total_tokens, stop_reasons.
    """
    jsonl_path = workflow_logs_dir(workflow_id, ensure_dir=False) / "llm_token_usage.jsonl"

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
                if entry.get("workflow_id") != workflow_id or entry.get("stage") != stage:
                    continue
                turns += 1
                prompt_tokens += entry.get("prompt_tokens", 0) or 0
                completion_tokens += entry.get("completion_tokens", 0) or 0
                total_tokens += entry.get("total_tokens", 0) or 0
                reason = entry.get("stop_reason")
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
