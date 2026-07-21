"""Injection-time synthesizer: render retrieved context items into a structured document.

The synthesizer sits between ``retrieve_context_items()`` and the injection
point (planner / code generator).  It accepts a ``list[ContextItem]`` plus a
``TicketContext`` describing the current task and produces a ``SynthesizedBlock``
— a structured markdown document with fixed sections and a provenance manifest
mapping each section to the source item ids that contributed to it.

Paraphrase variants of the same rule collapse naturally through the LLM call;
scope conditions and rationale are preserved because the LLM chooses how to
weave them.  Conflict pairs (``ContextItem.conflicts_with``) are surfaced
rather than silently resolved.

**Caching.** Every synthesis result is persisted in the ``context_block_cache``
SQLite table.  The cache key is
``SHA-256(ticket_key + "|" + filter_predicate + "|" + corpus_snapshot_id + "|" + recipe_target)``
where ``corpus_snapshot_id`` is the maximum ``updated_at`` across the retrieved
items (changes whenever the store changes, invalidating the cache implicitly).
An empty item list short-circuits the LLM call and returns an empty block.

**Feature flag.**  The synthesizer is gated by ``ACESettings.synthesizer_enabled``
(env var ``ACE_SYNTHESIZER_ENABLED``).  Callers check the flag before calling;
``synthesize_context_block`` itself is flag-agnostic and always runs when called.

Model selection: reads ``ACE_SYNTHESIZER_MODEL``, falling back to ``GOOSE_MODEL``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, cast

import litellm
from litellm import ModelResponse

from ace.models import ContextItem
from orchestrator.utils import litellm_call_kwargs
from state.sqlite_state_store import get_connection

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "synthesize.md"

# Section names produced by the LLM — must match the prompt contract.
SECTIONS = ("development_rules", "architectural_approach", "testing_approach", "known_pitfalls")


class SynthesizerError(Exception):
    """Raised when the LLM call or response parse fails."""


@dataclass(frozen=True)
class TicketContext:
    """Context about the current task passed to the synthesizer.

    ``recipe_target`` distinguishes planner from code-generator injection so
    the synthesizer can emphasise different sections and so the cache key is
    per injection point.
    """

    ticket_key: str
    ticket_summary: str = ""
    repo: Optional[str] = None
    project: Optional[str] = None
    platform: Optional[str] = None
    recipe_target: str = "planner"

    def filter_predicate(self) -> str:
        """Stable string representation of the applicability filter dimensions.

        Used as part of the cache key: if any dimension changes, the cache is
        effectively invalidated for this ticket context.
        """
        return f"project={self.project}|repo={self.repo}|platform={self.platform}"


@dataclass
class SynthesizedBlock:
    """The synthesizer's output: rendered markdown sections plus provenance.

    ``sections`` maps section name → markdown string.  Only sections with
    relevant content are present; callers must not assume all four sections
    exist.

    ``provenance`` maps section name → list of source ``ContextItem`` ids that
    contributed to that section.  Used by utilization telemetry (AOS-239) to
    record which staged items shaped planner behaviour.
    """

    sections: dict[str, str] = field(default_factory=dict)
    provenance: dict[str, list[str]] = field(default_factory=dict)

    def to_markdown(self) -> str:
        """Render the block as a single markdown document for prompt injection.

        Sections are emitted in canonical order (``SECTIONS``) with a level-2
        heading.  Missing sections are skipped.
        """
        section_titles = {
            "development_rules": "Development rules",
            "architectural_approach": "Architectural approach",
            "testing_approach": "Testing approach",
            "known_pitfalls": "Known pitfalls",
        }
        parts: list[str] = []
        for key in SECTIONS:
            if key in self.sections and self.sections[key].strip():
                title = section_titles.get(key, key.replace("_", " ").title())
                parts.append(f"## {title}\n\n{self.sections[key].strip()}")
        return "\n\n".join(parts)

    def is_empty(self) -> bool:
        return not any(v.strip() for v in self.sections.values())


def synthesize_context_block(
    items: list[ContextItem],
    ticket_context: TicketContext,
) -> SynthesizedBlock:
    """Render *items* into a structured guidance block for *ticket_context*.

    Returns an empty ``SynthesizedBlock`` when *items* is empty (safe no-op).
    Raises ``SynthesizerError`` on LLM call or parse failure.

    The result is cached in ``context_block_cache``; a cache hit skips the LLM
    call entirely.
    """
    if not items:
        return SynthesizedBlock()

    corpus_snapshot_id = max(item.updated_at for item in items)
    cache_key = _make_cache_key(ticket_context, corpus_snapshot_id)

    cached = _load_from_cache(cache_key)
    if cached is not None:
        logger.debug(
            "synthesizer cache hit for ticket=%s target=%s",
            ticket_context.ticket_key,
            ticket_context.recipe_target,
        )
        return cached

    logger.debug(
        "synthesizer cache miss — calling LLM for ticket=%s target=%s items=%d",
        ticket_context.ticket_key,
        ticket_context.recipe_target,
        len(items),
    )
    block = _call_llm_and_parse(items, ticket_context)
    _save_to_cache(cache_key, block, items, ticket_context)
    return block


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _make_cache_key(ticket_context: TicketContext, corpus_snapshot_id: str) -> str:
    """Stable SHA-256 cache key for (ticket_context, corpus_snapshot_id)."""
    raw = "|".join(
        [
            ticket_context.ticket_key,
            ticket_context.filter_predicate(),
            corpus_snapshot_id,
            ticket_context.recipe_target,
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _load_from_cache(cache_key: str) -> Optional[SynthesizedBlock]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT rendered_markdown, provenance_manifest FROM context_block_cache"
            " WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    provenance: dict[str, list[str]] = json.loads(row["provenance_manifest"] or "{}")
    # Re-materialise sections from the stored markdown by reverse-parsing the
    # rendered document.  This avoids storing sections separately and keeps the
    # cache schema minimal.
    sections = _parse_markdown_sections(row["rendered_markdown"])
    return SynthesizedBlock(sections=sections, provenance=provenance)


def _save_to_cache(
    cache_key: str,
    block: SynthesizedBlock,
    items: list[ContextItem],
    ticket_context: TicketContext,
) -> None:
    from datetime import UTC, datetime

    now = datetime.now(UTC).isoformat()
    input_ids = json.dumps([item.id for item in items])
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO context_block_cache
                (cache_key, rendered_markdown, provenance_manifest,
                 ticket_key, recipe_target, input_item_ids, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cache_key,
                block.to_markdown(),
                json.dumps(block.provenance),
                ticket_context.ticket_key,
                ticket_context.recipe_target,
                input_ids,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _parse_markdown_sections(markdown: str) -> dict[str, str]:
    """Reconstruct the sections dict from a rendered markdown document.

    Parses level-2 headings (``## Title``) as section boundaries and maps
    the canonical title back to the section key.
    """
    title_to_key = {
        "development rules": "development_rules",
        "architectural approach": "architectural_approach",
        "testing approach": "testing_approach",
        "known pitfalls": "known_pitfalls",
    }
    sections: dict[str, str] = {}
    current_key: Optional[str] = None
    current_lines: list[str] = []

    for line in markdown.splitlines():
        if line.startswith("## "):
            if current_key and current_lines:
                sections[current_key] = "\n".join(current_lines).strip()
            heading = line[3:].strip().lower()
            current_key = title_to_key.get(heading)
            current_lines = []
        elif current_key is not None:
            current_lines.append(line)

    if current_key and current_lines:
        sections[current_key] = "\n".join(current_lines).strip()

    return sections


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------


def _build_model() -> str:
    model = os.environ.get("ACE_SYNTHESIZER_MODEL") or os.environ.get("GOOSE_MODEL", "")
    if not model:
        raise SynthesizerError(
            "Neither ACE_SYNTHESIZER_MODEL nor GOOSE_MODEL is set — cannot invoke the Synthesizer."
        )
    return model


def _call_llm_and_parse(
    items: list[ContextItem], ticket_context: TicketContext
) -> SynthesizedBlock:
    model = _build_model()
    kwargs = litellm_call_kwargs(model)
    system_prompt = _PROMPT_PATH.read_text()

    items_payload = [
        {
            "id": item.id,
            "description": item.description,
            "pattern_type": item.pattern_type,
            "confidence": item.confidence,
            "evidence_count": item.evidence_count,
            "conflicts_with": item.conflicts_with,
        }
        for item in items
    ]
    user_payload = {
        "ticket_context": {
            "ticket_key": ticket_context.ticket_key,
            "ticket_summary": ticket_context.ticket_summary,
            "repo": ticket_context.repo,
            "project": ticket_context.project,
            "platform": ticket_context.platform,
            "recipe_target": ticket_context.recipe_target,
        },
        "items": items_payload,
    }

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(user_payload)},
    ]

    try:
        raw_response = litellm.completion(
            **kwargs,
            messages=messages,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        raise SynthesizerError(f"Synthesizer LLM call failed: {exc}") from exc

    if not hasattr(raw_response, "choices"):
        raise SynthesizerError(f"Unexpected litellm response type: {type(raw_response)}")

    response = cast(ModelResponse, raw_response)
    content = (response.choices[0].message.content or "").strip()
    if not content:
        raise SynthesizerError("Synthesizer LLM returned empty content")

    return _parse_response(content)


def _parse_response(raw: str) -> SynthesizedBlock:
    """Parse the LLM's JSON response into a SynthesizedBlock.

    Tolerates markdown fences (``` ... ```) wrapping the JSON.
    """
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        for part in parts[1:]:
            body = part.lstrip("json").lstrip("JSON").strip()
            if body.startswith("{"):
                text = body
                break

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SynthesizerError(
            f"Synthesizer returned invalid JSON: {exc}\nRaw: {raw[:200]}"
        ) from exc

    raw_sections = data.get("sections", {})
    raw_provenance = data.get("provenance", {})

    if not isinstance(raw_sections, dict):
        raise SynthesizerError(f"'sections' must be a dict, got {type(raw_sections)}")
    if not isinstance(raw_provenance, dict):
        raise SynthesizerError(f"'provenance' must be a dict, got {type(raw_provenance)}")

    sections: dict[str, str] = {}
    for key in SECTIONS:
        if key in raw_sections and isinstance(raw_sections[key], str) and raw_sections[key].strip():
            sections[key] = raw_sections[key]

    provenance: dict[str, list[str]] = {}
    for key in SECTIONS:
        if key in raw_provenance and isinstance(raw_provenance[key], list):
            provenance[key] = [str(v) for v in raw_provenance[key]]

    return SynthesizedBlock(sections=sections, provenance=provenance)


__all__ = [
    "TicketContext",
    "SynthesizedBlock",
    "SynthesizerError",
    "SECTIONS",
    "synthesize_context_block",
]
