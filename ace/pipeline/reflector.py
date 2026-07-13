"""Reflector: LLM candidate extraction from a workflow trace.

Reads a :class:`~ace.pipeline.trace_reader.TraceBundle`, calls an LLM with the
system prompt at ``ace/pipeline/prompts/reflector_system.md``, and returns a
list of :class:`~ace.models.CandidateItem` — the Curator's input (ticket 2.4).

Contract (per AOS-225 design):

- Returns ``list[CandidateItem]``; an empty list means "trace produced no
  generalisable signal" (the correct answer for trivial traces).
- Raises :class:`ReflectorError` on API failures or on parse/validation
  failures that persist through one retry. The runner (ticket 2.5) is
  expected to wrap the call in try/except and emit a
  ``learning_pipeline_failed`` audit event on failure (topic-09 § failure
  isolation).

Model selection: reads ``ACE_REFLECTOR_MODEL``, falling back to
``GOOSE_MODEL``. Kept as an env var here so ticket 4.2 can promote it into
:mod:`ace.config` without changing the call site.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional, cast

import litellm
from litellm import ModelResponse

from ace.models import CandidateItem
from ace.pipeline.trace_reader import TraceBundle
from orchestrator.utils import litellm_call_kwargs

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "reflector_system.md"
_SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")

_ALLOWED_PATTERN_TYPES = {"approach", "concern", "test_coverage", "implementation"}
_ALLOWED_SCOPES = {"task_type", "file_pattern", "codebase_wide"}
_ALLOWED_TIERS = {"TENTATIVE", "PATTERN", "ESTABLISHED"}
_MAX_CANDIDATES = 5


class ReflectorError(RuntimeError):
    """Raised when the Reflector cannot produce a valid candidate list.

    Distinct from ``list[CandidateItem] == []`` (a valid "no signal" result).
    """


def reflect(bundle: TraceBundle) -> list[CandidateItem]:
    """Extract context-item candidates from *bundle* via one LLM call.

    Returns an empty list if the LLM reports no generalisable signal.
    Raises :class:`ReflectorError` if the LLM call or parse fails after one
    retry.
    """
    model = _resolve_model()
    kwargs = litellm_call_kwargs(model)

    user_message = _render_user_message(bundle)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    last_error: Optional[Exception] = None
    for attempt in (1, 2):
        try:
            raw = _call_llm(kwargs, messages)
            data = _parse_json(raw)
            return _validate_candidates(data, workflow_id=bundle.workflow_id)
        except (ReflectorError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            logger.warning(
                "Reflector attempt %d failed for workflow %s: %s",
                attempt,
                bundle.workflow_id,
                exc,
            )
            # Only retry parse/validation errors. Genuine API errors surface
            # via the outer except below and are not retried here (litellm
            # already handles transient network failures internally).
            continue
        except Exception as exc:
            # Anything else (auth, quota, transport) — do not retry, wrap and
            # re-raise so the runner can decide what to do.
            raise ReflectorError(
                f"Reflector LLM call failed for workflow {bundle.workflow_id}: {exc}"
            ) from exc

    assert last_error is not None
    raise ReflectorError(
        f"Reflector failed to produce valid JSON for workflow {bundle.workflow_id} "
        f"after 2 attempts: {last_error}"
    ) from last_error


def _resolve_model() -> str:
    model = os.environ.get("ACE_REFLECTOR_MODEL") or os.environ.get("GOOSE_MODEL", "")
    if not model:
        raise ReflectorError(
            "Neither ACE_REFLECTOR_MODEL nor GOOSE_MODEL is set — " "cannot invoke the Reflector."
        )
    return model


def _call_llm(kwargs: dict, messages: list[dict]) -> str:
    """Invoke litellm and return the raw string content of choice[0].message."""
    raw_response = litellm.completion(
        **kwargs,
        messages=messages,
        temperature=0,
        response_format={"type": "json_object"},
    )
    if not hasattr(raw_response, "choices"):
        raise ReflectorError(f"Unexpected litellm response type: {type(raw_response)}")
    response = cast(ModelResponse, raw_response)
    choice = response.choices[0]
    content = (choice.message.content or "").strip()
    if not content:
        raise ValueError("LLM returned empty content")
    return content


def _parse_json(raw: str) -> dict:
    """Parse a JSON object out of *raw*, tolerating markdown fences.

    Mirrors the defensive parsing used in
    ``orchestrator.code_generator.nodes.infer_branch_prefix``: strip a leading
    ```` ```json ```` fence if present, then fall back to extracting the
    outermost ``{...}`` block.
    """
    text = raw.strip()
    if text.startswith("```"):
        # ```json ... ``` or ``` ... ```
        parts = text.split("```")
        # parts = ['', 'json\n...json body...\n', ''] or similar
        for part in parts[1:]:
            body = part.lstrip("json").lstrip("JSON").strip()
            if body.startswith("{"):
                text = body
                break
    if not text.startswith("{"):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            text = match.group()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")
    return data


def _validate_candidates(data: dict, *, workflow_id: str) -> list[CandidateItem]:
    """Turn the LLM's JSON dict into a list of validated :class:`CandidateItem`.

    Raises :class:`ValueError` on any structural or value violation so the
    retry loop in :func:`reflect` can catch it.
    """
    if "candidates" not in data:
        raise ValueError("Missing 'candidates' key in LLM response")
    raw_candidates = data["candidates"]
    if not isinstance(raw_candidates, list):
        raise ValueError(f"'candidates' must be a list, got {type(raw_candidates).__name__}")

    if len(raw_candidates) > _MAX_CANDIDATES:
        # The prompt caps at 5; truncate defensively rather than raise.
        logger.warning(
            "Reflector produced %d candidates for workflow %s; truncating to %d",
            len(raw_candidates),
            workflow_id,
            _MAX_CANDIDATES,
        )
        raw_candidates = raw_candidates[:_MAX_CANDIDATES]

    candidates: list[CandidateItem] = []
    for index, raw in enumerate(raw_candidates):
        candidate = _validate_one(raw, index=index, workflow_id=workflow_id)
        candidates.append(candidate)
    return candidates


def _validate_one(raw: Any, *, index: int, workflow_id: str) -> CandidateItem:
    if not isinstance(raw, dict):
        raise ValueError(f"Candidate {index} is not an object: {type(raw).__name__}")

    pattern_type = raw.get("pattern_type")
    if pattern_type not in _ALLOWED_PATTERN_TYPES:
        allowed = sorted(_ALLOWED_PATTERN_TYPES)
        raise ValueError(f"Candidate {index}: pattern_type={pattern_type!r} not in {allowed}")

    scope = raw.get("scope")
    if scope not in _ALLOWED_SCOPES:
        raise ValueError(f"Candidate {index}: scope={scope!r} not in {sorted(_ALLOWED_SCOPES)}")

    scope_value = raw.get("scope_value")
    if scope_value is not None and not isinstance(scope_value, str):
        raise ValueError(f"Candidate {index}: scope_value must be string or null")
    if scope == "codebase_wide" and scope_value:
        # codebase_wide items ignore scope_value; normalise for the Curator.
        scope_value = None
    if scope in ("task_type", "file_pattern") and not scope_value:
        raise ValueError(f"Candidate {index}: scope={scope!r} requires a non-empty scope_value")

    description = raw.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"Candidate {index}: description must be a non-empty string")

    initial_confidence = raw.get("initial_confidence")
    if not isinstance(initial_confidence, (int, float)):
        raise ValueError(
            f"Candidate {index}: initial_confidence must be a number, got "
            f"{type(initial_confidence).__name__}"
        )
    initial_confidence = float(initial_confidence)
    if not 0.5 <= initial_confidence <= 1.0:
        raise ValueError(
            f"Candidate {index}: initial_confidence={initial_confidence} outside [0.5, 1.0]"
        )

    suggested_tier = raw.get("suggested_tier")
    if suggested_tier is not None and suggested_tier not in _ALLOWED_TIERS:
        raise ValueError(
            f"Candidate {index}: suggested_tier={suggested_tier!r} not in {sorted(_ALLOWED_TIERS)}"
        )

    evidence_raw = raw.get("evidence", [])
    if not isinstance(evidence_raw, list):
        raise ValueError(f"Candidate {index}: evidence must be a list")
    evidence: list[dict[str, Any]] = []
    for e_index, entry in enumerate(evidence_raw):
        if not isinstance(entry, dict):
            raise ValueError(f"Candidate {index}, evidence {e_index}: not an object")
        signal_source = entry.get("signal_source")
        if not isinstance(signal_source, str) or not signal_source.strip():
            raise ValueError(f"Candidate {index}, evidence {e_index}: signal_source required")
        # The Curator (ticket 2.4) turns these into ProvenanceEntry records;
        # inject the workflow_id here so downstream code has the full audit
        # chain without re-looking-up the bundle.
        evidence.append(
            {
                "workflow_id": workflow_id,
                "signal_source": signal_source,
                "detail": entry.get("detail"),
            }
        )

    return CandidateItem(
        pattern_type=pattern_type,
        scope=scope,
        description=description.strip(),
        initial_confidence=initial_confidence,
        evidence=evidence,
        scope_value=scope_value,
        suggested_tier=suggested_tier,
    )


def _render_user_message(bundle: TraceBundle) -> str:
    """Assemble the trace into a compact JSON payload for the LLM.

    Passing structured JSON rather than free-form text keeps the prompt
    contract stable across schema changes to the source tables — the LLM
    reads keys, not phrasing.
    """
    payload = {
        "workflow_id": bundle.workflow_id,
        "ticket_key": bundle.ticket_key,
        "status": bundle.status,
        "created_at": bundle.created_at,
        "work_plan": bundle.work_plan,
        "code_generation_summary": bundle.code_generation_summary,
        "clarification_history": bundle.clarification_history,
        "pr_comments": bundle.pr_comments,
        "rejection_reason": bundle.rejection_reason,
    }
    return "TRACE:\n" + json.dumps(payload, indent=2, default=str)
