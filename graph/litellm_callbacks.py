"""LiteLLM proxy callbacks for token usage tracking."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

_WRITE_LOCK = Lock()


def _logs_dir() -> Path:
    default = Path(tempfile.gettempdir()) / "ngb-agent-orchestrator"
    base = Path(os.getenv("LOGS_DIR", str(default)))
    workflow_id = os.getenv("NGB_WORKFLOW_ID", "unknown")
    path = base / workflow_id
    path.mkdir(parents=True, exist_ok=True)
    return path


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


class TokenUsageLogger(CustomLogger):
    """Append one JSON line with usage stats for each completed LLM call."""

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

        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "workflow_id": os.getenv("NGB_WORKFLOW_ID", ""),
            "stage": os.getenv("NGB_WORKFLOW_STAGE", ""),
            "model": kwargs.get("model"),
            "request_id": response.get("id") or kwargs.get("litellm_call_id"),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "stop_reason": response.get("choices", [{}])[0].get("finish_reason")
            or response.get("stop_reason")
            or response.get("finish_reason"),
            "usage": usage,
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
    default = Path(tempfile.gettempdir()) / "ngb-agent-orchestrator"
    base = Path(os.getenv("LOGS_DIR", str(default)))
    jsonl_path = base / workflow_id / "llm_token_usage.jsonl"

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
