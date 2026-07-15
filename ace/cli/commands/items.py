"""``ace items list`` and ``ace items show`` command handlers.

Wraps :class:`AgentContextEngineService` behind the same seam used by
``ace.cli.commands.mine`` — no direct repository or pipeline access from here.
"""

from __future__ import annotations

from typing import Optional

import click

from ace.service import (
    AgentContextEngineService,
    ListItemsRequest,
    ShowItemRequest,
    ShowItemResult,
)

# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def _handle_items_list(
    service: AgentContextEngineService,
    *,
    status: Optional[str],
    pattern_type: Optional[str],
    scope: Optional[str],
    confidence_tier: Optional[str],
) -> None:
    """Fetch and print a filtered table of context items."""
    request = ListItemsRequest(
        status=status,
        pattern_type=pattern_type,
        scope=scope,
        confidence_tier=confidence_tier,
    )
    result = service.list_items(request)
    if not result.items:
        click.echo("no items found")
        return
    click.echo(_format_items_list(result.items))


def _format_items_list(
    items: tuple,
) -> str:
    """Render items as a compact aligned table."""
    lines = []
    for item in items:
        tier_label = f"[{item.confidence_tier}]" if item.confidence_tier else "[–]"
        scope_display = item.scope_value if item.scope_value else item.scope
        desc_preview = (
            item.description[:72] + "…" if len(item.description) > 72 else item.description
        )
        lines.append(
            f"{item.id[:8]}  {item.pattern_type:<16} {tier_label:<14} "
            f"{item.status:<12} {scope_display:<24} {desc_preview}"
        )
    header = f"{'ID':8}  {'PATTERN_TYPE':<16} {'TIER':<14} {'STATUS':<12} {'SCOPE':<24} DESCRIPTION"
    separator = "-" * len(header)
    return "\n".join([header, separator] + lines)


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def _handle_items_show(
    service: AgentContextEngineService,
    *,
    item_id: str,
) -> None:
    """Fetch and render one item's description and full provenance chain."""
    request = ShowItemRequest(item_id=item_id)
    result = service.show_item(request)
    if result is None:
        click.echo(f"item not found: {item_id}", err=True)
        raise SystemExit(1)
    click.echo(_format_item_detail(result))


def _format_item_detail(item: ShowItemResult) -> str:
    """Render an item's full detail including provenance chain."""
    tier_label = f"[{item.confidence_tier}]" if item.confidence_tier else "[–]"
    applicability_parts = []
    if item.project:
        applicability_parts.append(f"project={item.project}")
    if item.repo:
        applicability_parts.append(f"repo={item.repo}")
    if item.platform:
        applicability_parts.append(f"platform={item.platform}")
    applicability = ", ".join(applicability_parts) if applicability_parts else "all"

    scope_display = f"{item.scope}"
    if item.scope_value:
        scope_display += f":{item.scope_value}"

    lines = [
        f"id:           {item.id}",
        f"pattern_type: {item.pattern_type}",
        f"scope:        {scope_display}",
        f"status:       {item.status}",
        f"confidence:   {item.confidence:.2f}  {tier_label}",
        f"applies_to:   {applicability}",
        f"last_validated: {item.last_validated}",
        f"created_at:   {item.created_at}",
        f"updated_at:   {item.updated_at}",
        "",
        "description:",
        f"  {item.description}",
    ]

    if item.conflicts_with:
        lines += ["", "conflicts_with:", *(f"  - {cid}" for cid in item.conflicts_with)]

    lines += ["", f"provenance ({len(item.provenance)} event(s)):"]
    if not item.provenance:
        lines.append("  (none)")
    else:
        for i, ev in enumerate(item.provenance, start=1):
            lines.append(
                f"  [{i}] source={ev.signal_source}  date={ev.workflow_date}"
                f"  delta=+{ev.contributed_confidence:.2f}"
            )
            if ev.workflow_id:
                lines.append(f"       workflow_id={ev.workflow_id}")
            if ev.ticket_key:
                lines.append(f"       ticket={ev.ticket_key}")
            if ev.signal_detail:
                lines.append(f"       detail: {ev.signal_detail}")

    return "\n".join(lines)
