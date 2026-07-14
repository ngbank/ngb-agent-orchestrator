"""ACE domain models: ``ContextItem``, ``ProvenanceEntry``, ``CandidateItem``.

``ContextItem`` mirrors the row shape shared by the ``context_items`` (live) and
``context_items_staged`` tables from migration 014 — see
``docs/ACE/11-ace-orchestrator-data-model.md``. ``ProvenanceEntry`` is one
evidence event in a context item's ``provenance`` JSON array, per the same doc.
``CandidateItem`` is the raw Reflector output described in
``docs/ACE/09-ace-orchestrator-learning-pipeline.md``, before the Curator
triages it into a create/merge/contradict decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Optional

PatternType = Literal["approach", "concern", "test_coverage", "implementation"]
Scope = Literal["task_type", "file_pattern", "codebase_wide"]
Status = Literal["active", "staged", "deprecated", "conflicted"]


@dataclass(frozen=True)
class ProvenanceEntry:
    """One evidence event in a context item's provenance chain.

    ``workflow_date`` (not extraction date) anchors the decay model — see the
    schema doc's rationale for why the source date must be used even for
    freshly-extracted items.
    """

    signal_source: str
    workflow_date: str
    contributed_confidence: float
    workflow_id: Optional[str] = None
    ticket_key: Optional[str] = None
    signal_detail: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "ticket_key": self.ticket_key,
            "signal_source": self.signal_source,
            "signal_detail": self.signal_detail,
            "workflow_date": self.workflow_date,
            "contributed_confidence": self.contributed_confidence,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProvenanceEntry":
        return cls(
            signal_source=data["signal_source"],
            workflow_date=data["workflow_date"],
            contributed_confidence=data["contributed_confidence"],
            workflow_id=data.get("workflow_id"),
            ticket_key=data.get("ticket_key"),
            signal_detail=data.get("signal_detail"),
        )


@dataclass
class ContextItem:
    """A learned behavioral pattern, live (``context_items``) or staged.

    The staged-only fields (``review_notes``, ``promoted_at``, ``rejected_at``)
    are ``None`` for rows read from the live ``context_items`` table.

    ``project``, ``repo``, and ``platform`` are the applicability
    dimensions on staged and live items (see
    ``docs/ACE/11-ace-orchestrator-data-model.md``). ``None`` on any of
    them means "applies to all values on that axis" — the safe default that
    lets pre-existing items keep matching every workflow. ``project`` is a
    scope tag (typically a JIRA project short-name like ``"AOS"``), not a
    foreign key.

    ``conflicts_with`` is a list of ids of other items that give opposing
    guidance on the same subject. Populated by the Curator when contradiction
    is detected; consumed by retrieval / synthesizer to present both angles
    rather than block on ``conflicted`` status. Empty list = no known
    contradictions.
    """

    id: str
    pattern_type: PatternType
    scope: Scope
    description: str
    last_validated: str
    created_at: str
    updated_at: str
    scope_value: Optional[str] = None
    confidence: float = 0.5
    status: Status = "active"
    provenance: list[ProvenanceEntry] = field(default_factory=list)
    conflicts_with: list[str] = field(default_factory=list)
    review_notes: Optional[str] = None
    promoted_at: Optional[str] = None
    rejected_at: Optional[str] = None
    project: Optional[str] = None
    repo: Optional[str] = None
    platform: Optional[str] = None

    @property
    def evidence_count(self) -> int:
        """Number of workflow-evidence events that contributed to this item.

        Derived from ``len(provenance)`` — there is no separate stored counter.
        Any future cross-workflow strength signal will use a semantically
        distinct column name and its own audit trail.
        """
        return len(self.provenance)

    def to_row(self) -> dict[str, Any]:
        """Column values for an INSERT/UPDATE, keyed by column name."""
        return {
            "id": self.id,
            "pattern_type": self.pattern_type,
            "scope": self.scope,
            "scope_value": self.scope_value,
            "description": self.description,
            "confidence": self.confidence,
            "last_validated": self.last_validated,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "provenance": [entry.to_dict() for entry in self.provenance],
            "conflicts_with": list(self.conflicts_with),
            "review_notes": self.review_notes,
            "promoted_at": self.promoted_at,
            "rejected_at": self.rejected_at,
            "project": self.project,
            "repo": self.repo,
            "platform": self.platform,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "ContextItem":
        """Build a ``ContextItem`` from a ``sqlite3.Row`` (or plain dict).

        ``provenance`` and ``conflicts_with`` are read as JSON *string* columns
        and deserialized here; rows from either ``context_items`` or
        ``context_items_staged`` work — the staged-only keys simply default to
        ``None`` when absent.
        """
        import json

        raw_provenance = row["provenance"]
        entries = json.loads(raw_provenance) if raw_provenance else []
        keys = row.keys() if hasattr(row, "keys") else row
        raw_conflicts = row["conflicts_with"] if "conflicts_with" in keys else None
        conflicts_with = json.loads(raw_conflicts) if raw_conflicts else []
        return cls(
            id=row["id"],
            pattern_type=row["pattern_type"],
            scope=row["scope"],
            scope_value=row["scope_value"],
            description=row["description"],
            confidence=row["confidence"],
            last_validated=row["last_validated"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            status=row["status"],
            provenance=[ProvenanceEntry.from_dict(entry) for entry in entries],
            conflicts_with=conflicts_with,
            review_notes=row["review_notes"] if "review_notes" in keys else None,
            promoted_at=row["promoted_at"] if "promoted_at" in keys else None,
            rejected_at=row["rejected_at"] if "rejected_at" in keys else None,
            project=row["project"] if "project" in keys else None,
            repo=row["repo"] if "repo" in keys else None,
            platform=row["platform"] if "platform" in keys else None,
        )


@dataclass
class CandidateItem:
    """Raw Reflector output, before the Curator's create/merge/contradict triage.

    ``evidence`` entries are the pre-provenance shape from the Reflector prompt
    (``{"workflow_id", "signal_source", "detail"}``) — the Curator converts
    accepted evidence into full :class:`ProvenanceEntry` records on write.

    ``project`` / ``repo`` / ``platform`` are the applicability
    dimensions the Reflector emits. See :class:`ContextItem` for semantics;
    ``None`` means "applies to any value on that axis".
    """

    pattern_type: PatternType
    scope: Scope
    description: str
    initial_confidence: float
    evidence: list[dict[str, Any]] = field(default_factory=list)
    scope_value: Optional[str] = None
    suggested_tier: Optional[str] = None
    project: Optional[str] = None
    repo: Optional[str] = None
    platform: Optional[str] = None
