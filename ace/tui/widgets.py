"""Custom widgets for the ACE TUI."""

from __future__ import annotations

from typing import List, Optional

from rich.markup import escape
from textual.widgets import DataTable, Static

from ace.service.dtos import ItemSummaryDTO, ShowItemResult

_DESC_MAX = 60


class StagingQueueList(Static):
    """Scrollable staging-queue list rendered as a DataTable.

    Columns: Pattern Type | Scope | Confidence | Tier | Last Validated | Description

    The table is rebuilt on every :meth:`update_items` call.  The cursor
    position is preserved across refreshes by tracking the selected item id.
    """

    DEFAULT_CSS = """
    StagingQueueList {
        height: 100%;
        width: 100%;
    }
    DataTable {
        height: 100%;
        width: 100%;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._items: List[ItemSummaryDTO] = []

    def compose(self):
        table = DataTable(id="staging_table", cursor_type="row")
        table.add_columns(
            "Pattern Type",
            "Scope",
            "Confidence",
            "Tier",
            "Last Validated",
            "Description",
        )
        yield table

    def update_items(
        self,
        items: List[ItemSummaryDTO],
        *,
        sort_key: str = "confidence",
    ) -> None:
        """Rebuild the table with *items* sorted by *sort_key*.

        *sort_key* is one of ``"confidence"``, ``"age"``, or
        ``"pattern_type"``.  Any unrecognised value preserves the caller's
        original order.
        """
        sorted_items = _sort_items(items, sort_key)

        table = self.query_one("#staging_table", DataTable)

        # Preserve cursor position when the table is rebuilt on auto-refresh.
        selected_id: Optional[str] = None
        try:
            if table.cursor_row is not None and table.is_valid_row_index(table.cursor_row):
                selected_id = self._items[table.cursor_row].id
        except Exception:
            selected_id = None

        self._items = sorted_items
        table.clear()
        for item in sorted_items:
            tier = item.confidence_tier or ""
            desc = item.description
            if len(desc) > _DESC_MAX:
                desc = desc[:_DESC_MAX] + "\u2026"
            validated = (item.last_validated or "")[:19].replace("T", " ")
            table.add_row(
                item.pattern_type,
                item.scope,
                f"{item.confidence:.2f}",
                tier,
                validated,
                desc,
                key=item.id,
            )

        if not sorted_items:
            return

        selected_index = 0
        if selected_id is not None:
            for idx, it in enumerate(sorted_items):
                if it.id == selected_id:
                    selected_index = idx
                    break

        try:
            table.move_cursor(row=selected_index, column=0)
        except Exception:
            pass

    def get_selected_item(self) -> Optional[ItemSummaryDTO]:
        """Return the :class:`ItemSummaryDTO` for the highlighted row, or ``None``."""
        try:
            table = self.query_one("#staging_table", DataTable)
        except Exception:
            return None
        if table.cursor_row is None or table.row_count == 0:
            return None
        if not table.is_valid_row_index(table.cursor_row):
            return None
        if table.cursor_row >= len(self._items):
            return None
        return self._items[table.cursor_row]


def _sort_items(items: List[ItemSummaryDTO], sort_key: str) -> List[ItemSummaryDTO]:
    """Return *items* sorted by *sort_key*.

    * ``"confidence"`` — highest confidence first (descending).
    * ``"age"``        — oldest ``last_validated`` first (ascending ISO-8601).
    * ``"pattern_type"`` — alphabetical pattern type, then confidence desc.
    * Anything else   — original order preserved.
    """
    if sort_key == "confidence":
        return sorted(items, key=lambda x: x.confidence, reverse=True)
    if sort_key == "age":
        return sorted(items, key=lambda x: x.last_validated or "")
    if sort_key == "pattern_type":
        return sorted(items, key=lambda x: (x.pattern_type, -x.confidence))
    return list(items)


# ---------------------------------------------------------------------------
# Item detail pane
# ---------------------------------------------------------------------------

_TIER_COLOURS: dict[str, str] = {
    "ESTABLISHED": "green",
    "PATTERN": "yellow",
    "TENTATIVE": "cyan",
}


class ItemDetailPane(Static):
    """Scrollable detail pane for the currently-selected context item.

    Populated via :meth:`update_item` whenever the staging queue cursor moves.
    Shows the full description, provenance evidence chain, applicability
    dimensions, and any conflict IDs.
    """

    DEFAULT_CSS = """
    ItemDetailPane {
        height: 100%;
        width: 45%;
        padding: 1;
        border: solid $primary;
        overflow-y: auto;
    }
    ItemDetailPane Static#detail_body {
        height: auto;
    }
    """

    def compose(self):
        yield Static("Select an item to view details", id="detail_body")

    def update_item(self, item: Optional[ShowItemResult]) -> None:
        """Re-render the pane with *item*'s detail, or show a placeholder."""
        try:
            body = self.query_one("#detail_body", Static)
        except Exception:
            return
        if item is None:
            body.update("Select an item to view details")
            return
        body.update(_format_item_detail(item))


def _format_item_detail(item: ShowItemResult) -> str:
    """Build a Rich-markup string for *item*'s full detail view."""
    tier = item.confidence_tier or "—"
    tier_colour = _TIER_COLOURS.get(tier, "white")
    scope_label = item.scope
    if item.scope_value:
        scope_label = f"{item.scope} = {escape(item.scope_value)}"

    lines: list[str] = [
        f"[bold cyan]Pattern Type:[/bold cyan] {escape(item.pattern_type)}",
        f"[bold cyan]Scope:[/bold cyan]        {escape(scope_label)}",
        f"[bold cyan]Confidence:[/bold cyan]   {item.confidence:.2f} "
        f"[{tier_colour}][{escape(tier)}][/{tier_colour}]",
        f"[bold cyan]Status:[/bold cyan]       {escape(item.status)}",
        f"[bold cyan]Created:[/bold cyan]      {escape(item.created_at[:19].replace('T', ' '))}",
        f"[bold cyan]Updated:[/bold cyan]      {escape(item.updated_at[:19].replace('T', ' '))}",
        "",
        "[bold]Description[/bold]",
        "[dim]" + "─" * 40 + "[/dim]",
        escape(item.description),
    ]

    # Provenance chain
    lines += [
        "",
        "[bold]Provenance[/bold] ("
        + str(len(item.provenance))
        + (" event" if len(item.provenance) == 1 else " events")
        + ")",
        "[dim]" + "─" * 40 + "[/dim]",
    ]
    if item.provenance:
        for ev in item.provenance:
            date_part = escape(ev.workflow_date[:10]) if ev.workflow_date else "—"
            source_part = escape(ev.signal_source)
            conf_part = f"+{ev.contributed_confidence:.2f}"
            ticket_part = f" [{escape(ev.ticket_key)}]" if ev.ticket_key else ""
            lines.append(f"  [cyan]{source_part}[/cyan]{ticket_part}  {date_part}  {conf_part}")
            if ev.signal_detail:
                lines.append(f"    [dim]{escape(ev.signal_detail[:100])}[/dim]")
    else:
        lines.append("  [dim](none)[/dim]")

    # Applicability
    appl_parts = []
    if item.project:
        appl_parts.append(f"project={escape(item.project)}")
    if item.repo:
        appl_parts.append(f"repo={escape(item.repo)}")
    if item.platform:
        appl_parts.append(f"platform={escape(item.platform)}")
    if appl_parts:
        lines += [
            "",
            "[bold]Applicability[/bold]",
            "[dim]" + "─" * 40 + "[/dim]",
            "  " + "  ".join(appl_parts),
        ]

    # Conflicts
    if item.conflicts_with:
        lines += [
            "",
            "[bold]Conflicts With[/bold]",
            "[dim]" + "─" * 40 + "[/dim]",
        ]
        for cid in item.conflicts_with:
            lines.append(f"  [red]{escape(cid)}[/red]")

    return "\n".join(lines)
