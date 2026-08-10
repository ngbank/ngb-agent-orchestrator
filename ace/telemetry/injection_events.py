"""Persist ACE injection events without interrupting orchestration."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Iterable

from state.sqlite_state_store import get_connection

logger = logging.getLogger(__name__)


def record_injection_event(
    *,
    workflow_id: str,
    ticket_key: str | None,
    injection_point: str,
    synthesizer: str,
    block_cache_key: str | None,
    retrieved_item_ids: Iterable[str],
    rendered_length: int,
) -> None:
    """Record injection metadata, logging persistence errors without raising."""
    try:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO ace_injection_events
                    (workflow_id, ticket_key, injection_point, synthesizer,
                     block_cache_key, retrieved_item_ids, rendered_length, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workflow_id,
                    ticket_key,
                    injection_point,
                    synthesizer,
                    block_cache_key,
                    json.dumps(list(retrieved_item_ids)),
                    rendered_length,
                    datetime.now(UTC).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception(
            "Failed to persist ACE injection event for workflow=%s point=%s",
            workflow_id,
            injection_point,
        )
