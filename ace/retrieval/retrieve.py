"""``retrieve_context_items()``: the retrieval adapter over the live context store.

Per ``docs/ACE/11-ace-orchestrator-data-model.md`` ("What the retrieval query
looks like"): SQL handles scope and applicability filtering, Python handles
keyword ranking and the confidence-tier cutoff. Per the Epic 4 sequencing note
in ``docs/ACE/ace-implementation-plan.md`` (ticket 4.1), this adapter returns
raw :class:`~ace.models.ContextItem` rows — rendering into a prompt block is
the injection-time synthesizer's job (AOS-274), not retrieval's.
"""

from __future__ import annotations

import re
from typing import Optional

from ace.config import TIER_TENTATIVE_MIN
from ace.models import ContextItem
from state.sqlite_state_store import get_connection

# Common English words filtered out of keyword ranking so they don't inflate
# overlap scores between unrelated ticket/item text.
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "should",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "when",
        "will",
        "with",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    """Lowercase, word-tokenize, and strip stopwords from *text*."""
    return {tok for tok in _TOKEN_RE.findall(text.lower()) if tok not in _STOPWORDS}


def retrieve_context_items(
    *,
    task_type: Optional[str] = None,
    file_path: Optional[str] = None,
    query_text: str = "",
    top_k: int = 10,
    project: Optional[str] = None,
    repo: Optional[str] = None,
    platform: Optional[str] = None,
) -> list[ContextItem]:
    """Retrieve active context items relevant to the current workflow.

    Callers assemble *query_text* from whatever they have on hand at the call
    site (ticket content and task type at the planner, work-plan tasks and
    PR comments at the code generator — see
    ``docs/ACE/08-ace-orchestrator-injection-points.md``); this adapter itself
    stays agnostic to those shapes.

    Filtering happens in two stages:

    1. **SQL scope filter** (topic 11): live, ``status = 'active'`` items
       whose confidence clears the ``TENTATIVE`` floor, whose scope is
       ``codebase_wide`` or matches *task_type* / *file_path*, and whose
       applicability dimensions (``project`` / ``repo`` / ``platform``) are
       either unset (apply everywhere) or match the caller's values.
    2. **Python keyword ranking**: candidates are re-sorted by token overlap
       between *query_text* and each item's description, confidence as the
       tiebreaker, then truncated to *top_k*.

    An empty store — or a store with no matching rows — returns ``[]``, a
    safe no-op for callers.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT * FROM context_items
            WHERE status = 'active'
              AND confidence >= ?
              AND (
                  scope = 'codebase_wide'
                  OR (scope = 'task_type'    AND scope_value = ?)
                  OR (scope = 'file_pattern' AND ? LIKE scope_value)
              )
              AND (project  IS NULL OR project  = ?)
              AND (repo     IS NULL OR repo     = ?)
              AND (platform IS NULL OR platform = ?)
            """,
            (TIER_TENTATIVE_MIN, task_type, file_path, project, repo, platform),
        ).fetchall()
    finally:
        conn.close()

    candidates = [ContextItem.from_row(row) for row in rows]
    if not candidates:
        return []

    query_tokens = _tokenize(query_text)

    def _rank_key(item: ContextItem) -> tuple[int, float]:
        overlap = len(query_tokens & _tokenize(item.description))
        return (overlap, item.confidence)

    candidates.sort(key=_rank_key, reverse=True)
    return candidates[:top_k]


__all__ = ["retrieve_context_items"]
