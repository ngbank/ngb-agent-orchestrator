"""Curator: staging writes with create/merge/contradict logic.

Receives :class:`~ace.models.CandidateItem` candidates from the Reflector and
applies them to ``context_items_staged`` using one of three operations:

- **create**     — no existing staged item is sufficiently similar; write a new
  row with ``occurrence_count = 1``.
- **merge**      — an existing staged item is semantically similar and its
  guidance is compatible; increment ``occurrence_count``, append provenance,
  and recompute confidence as a weighted mean.
- **contradict** — an existing staged item covers the same subject but with
  opposing guidance (e.g. one says "always use X", the other says "never use
  X"); both rows are set to ``status='conflicted'`` for manual review.

Quality gate (applied before similarity search): descriptions that reference
run-specific artifacts (ticket keys, branch names, commit hashes) are
reformulated by stripping those references.  If the cleaned text shrinks below
:data:`_MIN_DESCRIPTION_LENGTH` characters the candidate is discarded.

ALL writes target ``context_items_staged``; the live ``context_items`` store is
never touched by the Curator.  Promotion to the live store happens via manual
review (Epic 3) or automated rules (Epic 5).

Keyword similarity uses Jaccard coefficient on tokenised word sets — no
embeddings yet (ticket 2.4 constraint).  Items with Jaccard score ≥
:data:`MERGE_THRESHOLD` are candidates for merge or contradiction; below the
threshold a new item is created.

``last_validated`` is always set to ``bundle.created_at`` (the *source*
workflow date) — not the extraction date.  This is the anchor for the decay
model described in ``docs/ACE/05-ace-curation-quality.md``.
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
    2. Searches existing staged items for the best keyword-similarity match,
       restricted to the same ``pattern_type``.
    3. Decides on an operation based on the best match score and polarity:

       - **create**    — score < :data:`MERGE_THRESHOLD`
       - **merge**     — score ≥ threshold and polarity is compatible
       - **contradict**— score ≥ threshold and polarity is opposing

    Returns a :class:`CurationResult` with per-operation counts.
    """
    result = CurationResult()
    if not candidates:
        return result

    existing = repo.list_staged()
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
        )

        best_match, best_score = _best_match(clean_candidate, existing)
        provenance_entry = _build_provenance_entry(clean_candidate, bundle)

        if best_score >= MERGE_THRESHOLD and best_match is not None:
            if _is_contradiction(clean_candidate.description, best_match.description):
                repo.update_staged_status(best_match.id, "conflicted")
                logger.info(
                    "Curator: contradiction flagged for staged item %s (score=%.2f)",
                    best_match.id,
                    best_score,
                )
                result.contradicted += 1
            else:
                new_confidence = (
                    best_match.confidence * best_match.occurrence_count
                    + clean_candidate.initial_confidence
                ) / (best_match.occurrence_count + 1)
                repo.merge_staged(
                    best_match.id,
                    new_confidence=new_confidence,
                    provenance_entry=provenance_entry,
                )
                logger.debug(
                    "Curator: merged candidate into staged item %s "
                    "(score=%.2f, new_confidence=%.3f)",
                    best_match.id,
                    best_score,
                    new_confidence,
                )
                result.merged += 1
        else:
            item = ContextItem(
                id=str(uuid.uuid4()),
                pattern_type=clean_candidate.pattern_type,
                scope=clean_candidate.scope,
                scope_value=clean_candidate.scope_value,
                description=clean_candidate.description,
                confidence=clean_candidate.initial_confidence,
                occurrence_count=1,
                last_validated=bundle.created_at,
                created_at=now,
                updated_at=now,
                status="staged",
                provenance=[provenance_entry],
            )
            repo.create_staged(item)
            logger.debug(
                "Curator: created new staged item (pattern_type=%s, confidence=%.2f)",
                item.pattern_type,
                item.confidence,
            )
            result.created += 1

    return result


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
    existing: list[ContextItem],
) -> tuple[Optional[ContextItem], float]:
    """Return ``(best_item, score)`` for *candidate* against *existing* staged items.

    Only items with the same ``pattern_type`` are considered — lessons of
    different types are orthogonal and must not be merged.
    """
    candidate_tokens = _tokenise(candidate.description)
    best: Optional[ContextItem] = None
    best_score = 0.0
    for item in existing:
        if item.pattern_type != candidate.pattern_type:
            continue
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
