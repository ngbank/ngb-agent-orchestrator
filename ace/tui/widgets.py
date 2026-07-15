"""Custom widgets for the ACE TUI."""

from __future__ import annotations

from typing import List, Optional

from textual.widgets import DataTable, Static

from ace.service.dtos import ItemSummaryDTO

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
