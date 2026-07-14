"""SQLiteContextItemRepository: CRUD + staging lifecycle over ``context_items``
and ``context_items_staged`` (migration 014).

Connection management is delegated to :func:`state.sqlite_state_store.get_connection`,
the same shared DB layer used by ``state.sqlite_workflow_repository`` — ACE owns
these two tables but never touches the ``workflows`` schema.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Optional

from ace.models import ContextItem, ProvenanceEntry, Scope
from state.sqlite_state_store import get_connection

_LIVE_TABLE = "context_items"
_STAGED_TABLE = "context_items_staged"

# Fixed weight for a human "promote" decision (ACE 11-ace-orchestrator-data-model.md):
# the reviewer is making a binary call, not providing a probability estimate, so a
# fixed contribution is more honest than treating it as a calibrated signal.
PROMOTION_CONFIDENCE_BOOST = 0.20


class ContextItemRepository:
    """Concrete repository over ``context_items`` / ``context_items_staged``."""

    # ------------------------------------------------------------------
    # Live store: context_items
    # ------------------------------------------------------------------

    def create(self, item: ContextItem) -> str:
        """Insert *item* into the live store and return its id.

        *item.id* must already be set by the caller (e.g. ``str(uuid.uuid4())``)
        — the repository does not generate ids so that callers can control
        identity (for example, reusing a staged item's id when promoting it).
        """
        row = item.to_row()
        conn = get_connection()
        try:
            conn.execute(
                f"""
                INSERT INTO {_LIVE_TABLE} (
                    id, pattern_type, scope, scope_value, description,
                    confidence, last_validated,
                    created_at, updated_at, status, provenance,
                    conflicts_with,
                    project, repo, platform
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["pattern_type"],
                    row["scope"],
                    row["scope_value"],
                    row["description"],
                    row["confidence"],
                    row["last_validated"],
                    row["created_at"],
                    row["updated_at"],
                    row["status"],
                    json.dumps(row["provenance"]),
                    json.dumps(row["conflicts_with"]),
                    row["project"],
                    row["repo"],
                    row["platform"],
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return item.id

    def get(self, item_id: str) -> Optional[ContextItem]:
        """Retrieve a live item by id, or ``None`` if not found."""
        conn = get_connection()
        try:
            row = conn.execute(f"SELECT * FROM {_LIVE_TABLE} WHERE id = ?", (item_id,)).fetchone()
        finally:
            conn.close()
        return ContextItem.from_row(row) if row is not None else None

    def list_items(
        self,
        *,
        pattern_type: Optional[str] = None,
        scope: Optional[str] = None,
        status: Optional[str] = None,
        min_confidence: Optional[float] = None,
    ) -> list[ContextItem]:
        """List live items, optionally filtered, ordered by confidence DESC."""
        clauses = []
        params: list = []
        if pattern_type is not None:
            clauses.append("pattern_type = ?")
            params.append(pattern_type)
        if scope is not None:
            clauses.append("scope = ?")
            params.append(scope)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if min_confidence is not None:
            clauses.append("confidence >= ?")
            params.append(min_confidence)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

        conn = get_connection()
        try:
            rows = conn.execute(
                f"SELECT * FROM {_LIVE_TABLE} {where} ORDER BY confidence DESC",
                params,
            ).fetchall()
        finally:
            conn.close()
        return [ContextItem.from_row(row) for row in rows]

    def update_confidence(self, item_id: str, confidence: float) -> None:
        """Overwrite a live item's confidence score."""
        self._update_confidence(_LIVE_TABLE, item_id, confidence)

    def append_provenance(self, item_id: str, entry: ProvenanceEntry) -> None:
        """Append *entry* to a live item's provenance chain."""
        self._append_provenance(_LIVE_TABLE, item_id, entry)

    def set_status(self, item_id: str, status: str) -> None:
        """Soft-transition a live item's status (e.g. 'deprecated', 'conflicted').

        Items are never hard-deleted — this is the only mechanism for removing
        an item from retrieval while preserving its audit trail.
        """
        now = datetime.now(UTC).isoformat()
        conn = get_connection()
        try:
            conn.execute(
                f"UPDATE {_LIVE_TABLE} SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, item_id),
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Staging: context_items_staged
    # ------------------------------------------------------------------

    def create_staged(self, item: ContextItem) -> str:
        """Insert *item* into staging and return its id.

        As with :meth:`create`, *item.id* must already be set by the caller.
        """
        row = item.to_row()
        conn = get_connection()
        try:
            conn.execute(
                f"""
                INSERT INTO {_STAGED_TABLE} (
                    id, pattern_type, scope, scope_value, description,
                    confidence, last_validated,
                    created_at, updated_at, status, provenance,
                    conflicts_with,
                    review_notes, promoted_at, rejected_at,
                    project, repo, platform
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["pattern_type"],
                    row["scope"],
                    row["scope_value"],
                    row["description"],
                    row["confidence"],
                    row["last_validated"],
                    row["created_at"],
                    row["updated_at"],
                    row["status"],
                    json.dumps(row["provenance"]),
                    json.dumps(row["conflicts_with"]),
                    row["review_notes"],
                    row["promoted_at"],
                    row["rejected_at"],
                    row["project"],
                    row["repo"],
                    row["platform"],
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return item.id

    def get_staged(self, item_id: str) -> Optional[ContextItem]:
        """Retrieve a staged item by id, or ``None`` if not found."""
        conn = get_connection()
        try:
            row = conn.execute(f"SELECT * FROM {_STAGED_TABLE} WHERE id = ?", (item_id,)).fetchone()
        finally:
            conn.close()
        return ContextItem.from_row(row) if row is not None else None

    def list_staged(self, *, pending_only: bool = False) -> list[ContextItem]:
        """List staged items, optionally restricted to those awaiting review.

        ``pending_only=True`` excludes items already promoted or rejected —
        i.e. the human review queue.
        """
        where = "WHERE promoted_at IS NULL AND rejected_at IS NULL" if pending_only else ""
        conn = get_connection()
        try:
            rows = conn.execute(
                f"SELECT * FROM {_STAGED_TABLE} {where} ORDER BY created_at ASC"
            ).fetchall()
        finally:
            conn.close()
        return [ContextItem.from_row(row) for row in rows]

    def list_staged_by_pattern_type(
        self, pattern_type: str, *, pending_only: bool = True
    ) -> list[ContextItem]:
        """List staged items of a given ``pattern_type``, filtered in SQL.

        Used by the Curator's similarity lookup (AOS-273): cross-pattern_type
        items are orthogonal and can never merge, so pushing the filter into
        SQL avoids materialising unrelated rows.  Defaults to
        ``pending_only=True`` because the only in-repo caller — the Curator —
        is scanning the human review queue.
        """
        clauses = ["pattern_type = ?"]
        params: list = [pattern_type]
        if pending_only:
            clauses.append("promoted_at IS NULL")
            clauses.append("rejected_at IS NULL")
        where = "WHERE " + " AND ".join(clauses)
        conn = get_connection()
        try:
            rows = conn.execute(
                f"SELECT * FROM {_STAGED_TABLE} {where} ORDER BY created_at ASC",
                params,
            ).fetchall()
        finally:
            conn.close()
        return [ContextItem.from_row(row) for row in rows]

    def update_staged_confidence(self, item_id: str, confidence: float) -> None:
        """Overwrite a staged item's confidence score."""
        self._update_confidence(_STAGED_TABLE, item_id, confidence)

    def append_staged_provenance(self, item_id: str, entry: ProvenanceEntry) -> None:
        """Append *entry* to a staged item's provenance chain."""
        self._append_provenance(_STAGED_TABLE, item_id, entry)

    def update_staged_status(self, item_id: str, status: str) -> None:
        """Set the status column on a staged item (e.g. ``'conflicted'``).

        Items are never hard-deleted from staging — status transitions preserve
        the full audit trail while controlling which items appear in the review
        queue.
        """
        now = datetime.now(UTC).isoformat()
        conn = get_connection()
        try:
            conn.execute(
                f"UPDATE {_STAGED_TABLE} SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, item_id),
            )
            conn.commit()
        finally:
            conn.close()

    def flag_conflict(self, *, staged_id: str, other_id: str) -> None:
        """Symmetrically record a contradiction between two *staged* items.

        Appends *other_id* to ``staged_id``'s ``conflicts_with`` array and
        vice versa in a single transaction (AOS-273). Idempotent: an id
        already present in the target array is not appended twice.

        Both items are expected to be in the staging table — this is the only
        place the Curator writes conflict edges. If either id is missing the
        method is a no-op for that side (the missing row will not appear in
        retrieval anyway).
        """
        now = datetime.now(UTC).isoformat()
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for target_id, added_id in (
                    (staged_id, other_id),
                    (other_id, staged_id),
                ):
                    row = conn.execute(
                        f"SELECT conflicts_with FROM {_STAGED_TABLE} WHERE id = ?",
                        (target_id,),
                    ).fetchone()
                    if row is None:
                        continue
                    current = json.loads(row["conflicts_with"]) if row["conflicts_with"] else []
                    if added_id in current:
                        continue
                    current.append(added_id)
                    conn.execute(
                        f"UPDATE {_STAGED_TABLE} "
                        "SET conflicts_with = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(current), now, target_id),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        finally:
            conn.close()

    def promote(
        self,
        item_id: str,
        *,
        review_notes: Optional[str] = None,
        scope: Optional[Scope] = None,
        scope_value: Optional[str] = None,
        contributed_confidence: float = PROMOTION_CONFIDENCE_BOOST,
    ) -> str:
        """Promote a staged item into the live store.

        Per the schema doc: the approval is an additional evidence event
        appended to provenance, and the promoted confidence is
        ``min(confidence + contributed_confidence, 1.0)``. *scope*/*scope_value*
        let a reviewer narrow scope at promotion time; the staged row's
        ``status`` stays ``'staged'`` — only ``promoted_at`` marks the outcome,
        preserving the staging record for audit.

        Returns the id of the new live ``context_items`` row (same id as the
        staged item).
        """
        staged = self.get_staged(item_id)
        if staged is None:
            raise ValueError(f"No staged context item with id {item_id!r}")

        now = datetime.now(UTC).isoformat()
        approval_entry = ProvenanceEntry(
            signal_source="human_review",
            signal_detail="Manually promoted by reviewer",
            workflow_date=now,
            contributed_confidence=contributed_confidence,
        )
        promoted = ContextItem(
            id=staged.id,
            pattern_type=staged.pattern_type,
            scope=scope or staged.scope,
            scope_value=scope_value if scope_value is not None else staged.scope_value,
            description=staged.description,
            confidence=min(staged.confidence + contributed_confidence, 1.0),
            last_validated=staged.last_validated,
            created_at=staged.created_at,
            updated_at=now,
            status="active",
            provenance=[*staged.provenance, approval_entry],
            conflicts_with=list(staged.conflicts_with),
            project=staged.project,
            repo=staged.repo,
            platform=staged.platform,
        )

        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = promoted.to_row()
                conn.execute(
                    f"""
                    INSERT INTO {_LIVE_TABLE} (
                        id, pattern_type, scope, scope_value, description,
                        confidence, last_validated,
                        created_at, updated_at, status, provenance,
                        conflicts_with,
                        project, repo, platform
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["id"],
                        row["pattern_type"],
                        row["scope"],
                        row["scope_value"],
                        row["description"],
                        row["confidence"],
                        row["last_validated"],
                        row["created_at"],
                        row["updated_at"],
                        row["status"],
                        json.dumps(row["provenance"]),
                        json.dumps(row["conflicts_with"]),
                        row["project"],
                        row["repo"],
                        row["platform"],
                    ),
                )
                set_clauses = ["promoted_at = ?", "updated_at = ?"]
                params: list = [now, now]
                if review_notes is not None:
                    set_clauses.append("review_notes = ?")
                    params.append(review_notes)
                params.append(item_id)
                conn.execute(
                    f"UPDATE {_STAGED_TABLE} SET {', '.join(set_clauses)} WHERE id = ?",
                    params,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        finally:
            conn.close()

        return promoted.id

    def reject(self, item_id: str, *, review_notes: Optional[str] = None) -> None:
        """Mark a staged item as rejected, preserving the row for audit."""
        now = datetime.now(UTC).isoformat()
        set_clauses = ["rejected_at = ?", "updated_at = ?"]
        params: list = [now, now]
        if review_notes is not None:
            set_clauses.append("review_notes = ?")
            params.append(review_notes)
        params.append(item_id)

        conn = get_connection()
        try:
            conn.execute(
                f"UPDATE {_STAGED_TABLE} SET {', '.join(set_clauses)} WHERE id = ?",
                params,
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Shared helpers (table name always one of the two module constants above)
    # ------------------------------------------------------------------

    def _update_confidence(self, table: str, item_id: str, confidence: float) -> None:
        now = datetime.now(UTC).isoformat()
        conn = get_connection()
        try:
            conn.execute(
                f"UPDATE {table} SET confidence = ?, updated_at = ? WHERE id = ?",
                (confidence, now, item_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _append_provenance(self, table: str, item_id: str, entry: ProvenanceEntry) -> None:
        now = datetime.now(UTC).isoformat()
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    f"SELECT provenance FROM {table} WHERE id = ?", (item_id,)
                ).fetchone()
                if row is None:
                    conn.rollback()
                    return

                entries = json.loads(row["provenance"]) if row["provenance"] else []
                entries.append(entry.to_dict())

                conn.execute(
                    f"UPDATE {table} SET provenance = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(entries), now, item_id),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        finally:
            conn.close()


__all__ = [
    "ContextItemRepository",
    "PROMOTION_CONFIDENCE_BOOST",
]
