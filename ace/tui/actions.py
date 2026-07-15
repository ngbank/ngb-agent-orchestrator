"""Action handlers for the ACE TUI.

Promote and reject handlers call the :class:`AgentContextEngineService` and
raise :class:`ActionError` on failure so the TUI's notification layer can
surface a concise error message without crashing.
"""

from __future__ import annotations

from typing import Optional

from ace.service.dtos import PromoteRequest, RejectRequest
from ace.service.protocols import AgentContextEngineService


class ActionError(Exception):
    """Raised when a TUI action fails; surfaced as an error notification."""

    pass


def promote_item(
    service: AgentContextEngineService,
    item_id: str,
    *,
    notes: Optional[str] = None,
    scope: Optional[str] = None,
    scope_value: Optional[str] = None,
) -> str:
    """Promote *item_id* from staging to the live store.

    Returns a brief confirmation string for the TUI notification.
    Raises :class:`ActionError` on service failure.
    """
    try:
        result = service.promote(
            PromoteRequest(
                item_id=item_id,
                notes=notes,
                scope=scope,
                scope_value=scope_value,
            )
        )
        return f"Promoted {result.item_id[:8]}\u2026"
    except Exception as exc:
        raise ActionError(f"Promote failed: {exc}") from exc


def reject_item(
    service: AgentContextEngineService,
    item_id: str,
    *,
    notes: Optional[str] = None,
) -> str:
    """Reject *item_id* in the staging store (no hard delete).

    Returns a brief confirmation string for the TUI notification.
    Raises :class:`ActionError` on service failure.
    """
    try:
        result = service.reject(RejectRequest(item_id=item_id, notes=notes))
        return f"Rejected {result.item_id[:8]}\u2026"
    except Exception as exc:
        raise ActionError(f"Reject failed: {exc}") from exc
