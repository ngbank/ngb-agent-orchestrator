"""``ace promote`` and ``ace reject`` command handlers.

Wraps :class:`AgentContextEngineService` behind the same seam used by
``ace.cli.commands.items`` — no direct repository or pipeline access from here.
"""

from __future__ import annotations

from typing import Optional

import click

from ace.service import (
    AgentContextEngineService,
    PromoteRequest,
    RejectRequest,
)


def _handle_promote(
    service: AgentContextEngineService,
    *,
    item_id: str,
    notes: Optional[str],
    scope: Optional[str],
    scope_value: Optional[str],
) -> None:
    """Promote a staged item and report the resulting live item id."""
    request = PromoteRequest(
        item_id=item_id,
        notes=notes,
        scope=scope,
        scope_value=scope_value,
    )
    try:
        result = service.promote(request)
    except ValueError as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1)
    click.echo(f"promoted: {result.item_id}")


def _handle_reject(
    service: AgentContextEngineService,
    *,
    item_id: str,
    notes: Optional[str],
) -> None:
    """Mark a staged item as rejected."""
    request = RejectRequest(item_id=item_id, notes=notes)
    try:
        service.reject(request)
    except ValueError as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1)
    click.echo(f"rejected: {item_id}")
