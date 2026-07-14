"""Curator: quality gate + exact-dedup safety net + contradiction flag.

The Curator is deliberately small. Semantic consolidation moved to read
time (injection-time synthesizer). At mine time the Curator does three
things and nothing more:

- **quality gate** — descriptions that reference run-specific artifacts
  (ticket keys, branch names, commit hashes) are reformulated by stripping
  those references. If the cleaned text shrinks below
  :data:`_MIN_DESCRIPTION_LENGTH` characters the candidate is discarded.
- **exact-dedup safety net** — for the rare case where an in-flight batch
  produces a near-duplicate of an already-staged item (Jaccard ≥
  :data:`MERGE_THRESHOLD` on the same ``pattern_type``, same polarity), the
  candidate is folded into the existing row by appending a
  :class:`~ace.models.ProvenanceEntry`. **No confidence recomputation, no
  counter increment.** Confidence stays whatever the Reflector produced
  (modulo human review and decay); evidence count is derived from
  ``len(provenance)`` at read time.
- **contradiction flag** — same-subject + opposing polarity writes *both*
  rows to staging and populates ``conflicts_with`` symmetrically. Neither row
  is blocked with ``status='conflicted'``; the synthesizer decides at read
  time how to present the conflict pair.

Keyword similarity uses Jaccard coefficient on tokenised word sets — no
embeddings. Items with score ≥ :data:`MERGE_THRESHOLD` in the same
``pattern_type`` are candidates for merge or contradiction; below the
threshold a new item is created.

ALL writes target ``context_items_staged``; the live ``context_items`` store
is never touched by the Curator. Promotion to the live store happens via
manual review or automated rules.

``last_validated`` is always set to ``bundle.created_at`` (the *source*
workflow date) — not the extraction date. This is the anchor for the decay
model described in ``docs/ACE/05-ace-curation-quality.md``.

See ``docs/ACE/15-ace-injection-synthesizer.md`` for the read-time
consolidation design that motivates this trim.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Optional

from ace.models import CandidateItem, ContextItem, ProvenanceEntry
from ace.pipeline.trace_reader import TraceBundle
from ace.repository.context_item_repository import ContextItemRepository

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public thresholds (tests may override via monkeypatch)
# ---------------------------------------------------------------------------

#: Jaccard similarity at or above which two items are considered the same
#: lesson.  Items below this threshold get a new row in the staging store.
MERGE_THRESHOLD: float = 0.35

# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

#: Minimum character length for a description to be kept after quality-gate
#: reformulation.  Shorter strings are treated as non-generalisable.
_MIN_DESCRIPTION_LENGTH: int = 15

#: Patterns that indicate a run-specific reference in a description.
_RUN_SPECIFIC_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b[A-Z]+-\d+\b"),  # ticket keys: AOS-226, JIRA-1
    re.compile(r"\bfeature/\S+", re.I),  # feature branches
    re.compile(r"\bfix/\S+", re.I),  # fix branches
    re.compile(r"\bhot-?fix/\S+", re.I),  # hotfix branches
    re.compile(r"\b[0-9a-f]{7,40}\b"),  # commit hashes (7–40 hex chars)
]

#: Words that indicate a description's guidance is primarily negative /
#: avoidance-oriented.  Used to detect contradictions between candidates and
#: existing staged items.
_NEGATION_MARKERS: frozenset[str] = frozenset(
    [
        "never",
        "avoid",
        "don't",
        "do not",
        "must not",
        "should not",
        "prohibited",
        "unsafe",
        "incorrect",
        "wrong",
    ]
)

#: Common English stop words removed before tokenisation.
_STOP_WORDS: frozenset[str] = frozenset(
    [
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "should",
        "could",
        "may",
        "might",
        "can",
        "to",
        "for",
        "in",
        "on",
        "at",
        "by",
        "with",
        "from",
        "of",
        "and",
        "or",
        "but",
        "not",
        "it",
        "its",
        "we",
        "you",
        "they",
        "this",
        "that",
        "these",
        "those",
        "so",
        "if",
        "as",
        "when",
        "while",
        "then",
        "than",
    ]
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class CurationResult:
    """Summary of a single :func:`curate` call."""

    created: int = 0
    merged: int = 0
    contradicted: int = 0
    discarded: int = 0


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def curate(
    candidates: list[CandidateItem],
    bundle: TraceBundle,
    *,
    repo: ContextItemRepository,
) -> CurationResult:
    """Apply Reflector *candidates* to the staging store.

    For each candidate the Curator:

    1. Runs the quality gate (strip run-specific references; discard if too
       short).
    2. Searches existing pending staged items *of the same pattern_type* for
       the best keyword-similarity match (SQL-level filter — cross-pattern
       items are orthogonal by construction).
    3. Decides on an operation based on the best match score and polarity:

       - **create**       — score < :data:`MERGE_THRESHOLD`
       - **merge**        — score ≥ threshold and polarity is compatible;
         append a :class:`ProvenanceEntry` to the existing row (confidence
         and other columns are NOT touched).
       - **contradict**   — score ≥ threshold and polarity is opposing;
         write the candidate as a *new* pending staged row and symmetrically
         populate ``conflicts_with`` on both rows. Neither row is blocked
         with ``status='conflicted'`` — the read-time synthesizer decides
         how to present the pair.

    Returns a :class:`CurationResult` with per-operation counts.
    """
    result = CurationResult()
    if not candidates:
        return result

    now = datetime.now(UTC).isoformat()

    for candidate in candidates:
        cleaned = _quality_gate(candidate.description)
        if cleaned is None:
            logger.debug("Curator discarding run-specific candidate: %r", candidate.description)
            result.discarded += 1
            continue

        # Work with the quality-gated description from here on.
        clean_candidate = CandidateItem(
            pattern_type=candidate.pattern_type,
            scope=candidate.scope,
            scope_value=candidate.scope_value,
            description=cleaned,
            initial_confidence=candidate.initial_confidence,
            evidence=candidate.evidence,
            suggested_tier=candidate.suggested_tier,
            project=candidate.project,
            repo=candidate.repo,
            platform=candidate.platform,
        )

        # SQL-level pattern_type filter — cross-pattern items are orthogonal
        # and can never merge, so we push the filter into SQL.
        peers = repo.list_staged_by_pattern_type(clean_candidate.pattern_type, pending_only=True)
        best_match, best_score = _best_match(clean_candidate, peers)
        provenance_entry = _build_provenance_entry(clean_candidate, bundle)

        if best_score >= MERGE_THRESHOLD and best_match is not None:
            if _is_contradiction(clean_candidate.description, best_match.description):
                # Write BOTH rows as normal pending staged items and record
                # the contradiction symmetrically. The old design blocked the
                # existing item with status='conflicted' and silently dropped
                # the candidate; both were latent bugs.
                new_item = _build_new_staged_item(clean_candidate, bundle, provenance_entry, now)
                repo.create_staged(new_item)
                repo.flag_conflict(staged_id=new_item.id, other_id=best_match.id)
                logger.info(
                    "Curator: contradiction pair recorded (new=%s, existing=%s, score=%.2f)",
                    new_item.id,
                    best_match.id,
                    best_score,
                )
                result.contradicted += 1
            else:
                # Exact-dedup safety net: append provenance only. Confidence
                # is deliberately NOT recomputed — see module docstring.
                repo.append_staged_provenance(best_match.id, provenance_entry)
                logger.debug(
                    "Curator: merged candidate into staged item %s "
                    "(score=%.2f, evidence-only append)",
                    best_match.id,
                    best_score,
                )
                result.merged += 1
        else:
            item = _build_new_staged_item(clean_candidate, bundle, provenance_entry, now)
            repo.create_staged(item)
            logger.debug(
                "Curator: created new staged item (pattern_type=%s, confidence=%.2f)",
                item.pattern_type,
                item.confidence,
            )
            result.created += 1

    return result


def _build_new_staged_item(
    candidate: CandidateItem,
    bundle: TraceBundle,
    provenance_entry: ProvenanceEntry,
    now: str,
) -> ContextItem:
    """Construct a fresh staged :class:`ContextItem` from *candidate*.

    Shared by the create and contradict paths so both produce identical row
    shapes (differ only in the ``conflicts_with`` population that happens
    afterwards via :meth:`ContextItemRepository.flag_conflict`).
    """
    return ContextItem(
        id=str(uuid.uuid4()),
        pattern_type=candidate.pattern_type,
        scope=candidate.scope,
        scope_value=candidate.scope_value,
        description=candidate.description,
        confidence=candidate.initial_confidence,
        last_validated=bundle.created_at,
        created_at=now,
        updated_at=now,
        status="staged",
        provenance=[provenance_entry],
        project=candidate.project,
        repo=candidate.repo,
        platform=candidate.platform,
    )


# ---------------------------------------------------------------------------
# Quality gate
# ---------------------------------------------------------------------------


def _quality_gate(description: str) -> Optional[str]:
    """Strip run-specific references; return ``None`` if the result is too short.

    Ticket keys (``AOS-226``), branch names (``feature/my-branch``), and
    commit hashes are removed.  After stripping, extra whitespace is collapsed.
    If the cleaned description is shorter than :data:`_MIN_DESCRIPTION_LENGTH`
    characters it is treated as non-generalisable and ``None`` is returned.
    """
    cleaned = description
    for pattern in _RUN_SPECIFIC_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) < _MIN_DESCRIPTION_LENGTH:
        return None
    return cleaned


# ---------------------------------------------------------------------------
# Keyword similarity helpers
# ---------------------------------------------------------------------------


def _tokenise(text: str) -> frozenset[str]:
    """Return lower-case alphabetic tokens from *text*, excluding stop words."""
    words = re.findall(r"[a-z]+", text.lower())
    return frozenset(w for w in words if w not in _STOP_WORDS)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity between two token sets."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _best_match(
    candidate: CandidateItem,
    peers: list[ContextItem],
) -> tuple[Optional[ContextItem], float]:
    """Return ``(best_item, score)`` for *candidate* against *peers*.

    *peers* must already be filtered to the candidate's ``pattern_type`` —
    the caller does this in SQL via
    :meth:`ContextItemRepository.list_staged_by_pattern_type`, so this
    function does not re-check the pattern_type.
    """
    candidate_tokens = _tokenise(candidate.description)
    best: Optional[ContextItem] = None
    best_score = 0.0
    for item in peers:
        score = _jaccard(candidate_tokens, _tokenise(item.description))
        if score > best_score:
            best_score = score
            best = item
    return best, best_score


# ---------------------------------------------------------------------------
# Contradiction detection
# ---------------------------------------------------------------------------


def _is_contradiction(candidate_desc: str, existing_desc: str) -> bool:
    """Return ``True`` if the two descriptions have opposing polarity.

    Polarity is determined by the presence of :data:`_NEGATION_MARKERS` (never,
    avoid, do not, …).  When one description contains these markers and the
    other does not, the two items are giving opposing guidance on the same
    subject.
    """
    return _has_negation_polarity(candidate_desc) != _has_negation_polarity(existing_desc)


def _has_negation_polarity(text: str) -> bool:
    """Return ``True`` if *text* primarily expresses avoidance or prohibition."""
    lower = text.lower()
    return any(marker in lower for marker in _NEGATION_MARKERS)


# ---------------------------------------------------------------------------
# Provenance builder
# ---------------------------------------------------------------------------


def _build_provenance_entry(candidate: CandidateItem, bundle: TraceBundle) -> ProvenanceEntry:
    """Construct a :class:`ProvenanceEntry` for a Curator write.

    ``workflow_date`` is always ``bundle.created_at`` (the *source* workflow
    date) — never the extraction date.  See the data-model doc's rationale for
    why this must be the source date even for freshly-extracted items.
    """
    if candidate.evidence:
        first = candidate.evidence[0]
        signal_source = first.get("signal_source", "reflector")
        signal_detail = first.get("detail")
    else:
        signal_source = "reflector"
        signal_detail = None
    return ProvenanceEntry(
        workflow_id=bundle.workflow_id,
        ticket_key=bundle.ticket_key,
        signal_source=signal_source,
        signal_detail=signal_detail,
        workflow_date=bundle.created_at,
        contributed_confidence=candidate.initial_confidence,
    )
