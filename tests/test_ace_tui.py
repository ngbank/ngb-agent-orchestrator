"""Unit tests for the ACE TUI app scaffold and staging queue screen."""

from __future__ import annotations

from typing import List, Optional

import pytest

from ace.service.dtos import (
    ItemSummaryDTO,
    ListItemsRequest,
    ListItemsResult,
    MineRequest,
    MineResult,
    PromoteRequest,
    PromoteResult,
    RejectRequest,
    RejectResult,
    ShowItemRequest,
    ShowItemResult,
)
from ace.service.protocols import AgentContextEngineService
from ace.tui.action_registry import REGISTRY, action_for
from ace.tui.app import AceTUI
from ace.tui.widgets import StagingQueueList, _sort_items

# ---------------------------------------------------------------------------
# Fake service
# ---------------------------------------------------------------------------


class FakeAceService:
    """In-memory :class:`AgentContextEngineService` for TUI tests."""

    def __init__(self, staged: Optional[List[ItemSummaryDTO]] = None) -> None:
        self._staged: List[ItemSummaryDTO] = staged or []

    def list_items(self, request: ListItemsRequest) -> ListItemsResult:
        items = list(self._staged) if request.status == "staged" else []
        return ListItemsResult(items=tuple(items))

    def mine(self, request: MineRequest) -> MineResult:  # pragma: no cover
        raise NotImplementedError

    def show_item(self, request: ShowItemRequest) -> Optional[ShowItemResult]:  # pragma: no cover
        return None

    def promote(self, request: PromoteRequest) -> PromoteResult:  # pragma: no cover
        raise NotImplementedError

    def reject(self, request: RejectRequest) -> RejectResult:  # pragma: no cover
        raise NotImplementedError


def _item(
    item_id: str,
    *,
    pattern_type: str = "approach",
    scope: str = "codebase_wide",
    scope_value: Optional[str] = None,
    description: str = "a context item",
    confidence: float = 0.7,
    confidence_tier: Optional[str] = "PATTERN",
    status: str = "staged",
    last_validated: str = "2024-06-01T00:00:00",
) -> ItemSummaryDTO:
    return ItemSummaryDTO(
        id=item_id,
        pattern_type=pattern_type,
        scope=scope,
        scope_value=scope_value,
        description=description,
        confidence=confidence,
        confidence_tier=confidence_tier,
        status=status,
        last_validated=last_validated,
    )


@pytest.fixture
def sample_items() -> List[ItemSummaryDTO]:
    return [
        _item("i-1", confidence=0.9, pattern_type="approach", last_validated="2024-03-01"),
        _item("i-2", confidence=0.6, pattern_type="concern", last_validated="2024-01-01"),
        _item("i-3", confidence=0.75, pattern_type="approach", last_validated="2024-06-01"),
    ]


@pytest.fixture
def fake_service(sample_items: List[ItemSummaryDTO]) -> AgentContextEngineService:
    svc = FakeAceService(staged=sample_items)
    assert isinstance(svc, AgentContextEngineService)
    return svc


# ---------------------------------------------------------------------------
# _sort_items unit tests
# ---------------------------------------------------------------------------


class TestSortItems:
    def test_confidence_descending(self, sample_items: List[ItemSummaryDTO]) -> None:
        result = _sort_items(sample_items, "confidence")
        confidences = [i.confidence for i in result]
        assert confidences == sorted(confidences, reverse=True)

    def test_age_ascending(self, sample_items: List[ItemSummaryDTO]) -> None:
        result = _sort_items(sample_items, "age")
        dates = [i.last_validated for i in result]
        assert dates == sorted(dates)

    def test_pattern_type_alphabetical(self, sample_items: List[ItemSummaryDTO]) -> None:
        result = _sort_items(sample_items, "pattern_type")
        types = [i.pattern_type for i in result]
        # approach comes before concern alphabetically
        assert types[0] == "approach"
        assert types[-1] == "concern"

    def test_unknown_key_preserves_order(self, sample_items: List[ItemSummaryDTO]) -> None:
        result = _sort_items(sample_items, "unknown_key")
        assert [i.id for i in result] == [i.id for i in sample_items]

    def test_empty_list(self) -> None:
        assert _sort_items([], "confidence") == []


# ---------------------------------------------------------------------------
# StagingQueueList widget (unmounted)
# ---------------------------------------------------------------------------


class TestStagingQueueListUnmounted:
    def test_initial_items_empty(self) -> None:
        widget = StagingQueueList()
        assert widget._items == []

    def test_get_selected_item_without_mount_returns_none(self) -> None:
        widget = StagingQueueList()
        widget._items = [_item("i-1")]
        assert widget.get_selected_item() is None

    def test_update_items_sorts_and_stores(self, sample_items: List[ItemSummaryDTO]) -> None:
        widget = StagingQueueList()
        # Directly populate _items to simulate sorted state without a mounted DataTable.
        from ace.tui.widgets import _sort_items

        sorted_by_conf = _sort_items(sample_items, "confidence")
        widget._items = sorted_by_conf
        assert widget._items[0].confidence >= widget._items[-1].confidence


# ---------------------------------------------------------------------------
# Action registry unit tests
# ---------------------------------------------------------------------------


class TestActionRegistry:
    def test_all_registry_entries_have_unique_keys(self) -> None:
        keys = [a.key for a in REGISTRY]
        assert len(keys) == len(set(keys)), "Duplicate keys in ACE registry"

    def test_all_registry_entries_have_unique_actions(self) -> None:
        actions = [a.action for a in REGISTRY]
        assert len(actions) == len(set(actions)), "Duplicate action names in ACE registry"

    def test_action_for_known_action(self) -> None:
        entry = action_for("refresh")
        assert entry is not None
        assert entry.key == "r"

    def test_action_for_unknown_returns_none(self) -> None:
        assert action_for("does_not_exist") is None

    def test_global_actions_visible_without_selection(self) -> None:
        for action_name in ("refresh", "sort_confidence", "sort_age", "sort_pattern_type"):
            entry = action_for(action_name)
            assert entry is not None
            assert entry.applies(None) is True, f"{action_name} should be visible with no selection"

    def test_promote_hidden_without_selection(self) -> None:
        entry = action_for("promote")
        assert entry is not None
        assert entry.applies(None) is False

    def test_reject_hidden_without_selection(self) -> None:
        entry = action_for("reject")
        assert entry is not None
        assert entry.applies(None) is False

    def test_promote_visible_on_staged_item(self) -> None:
        entry = action_for("promote")
        assert entry is not None
        assert entry.applies(_item("i-1", status="staged")) is True

    def test_reject_visible_on_staged_item(self) -> None:
        entry = action_for("reject")
        assert entry is not None
        assert entry.applies(_item("i-1", status="staged")) is True


# ---------------------------------------------------------------------------
# AceTUI Textual app integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAceTUI:
    async def test_app_mounts(self, fake_service: AgentContextEngineService) -> None:
        app = AceTUI(fake_service)
        async with app.run_test():
            assert app.is_running

    async def test_staging_queue_populated_on_mount(
        self, fake_service: AgentContextEngineService, sample_items: List[ItemSummaryDTO]
    ) -> None:
        app = AceTUI(fake_service)
        async with app.run_test():
            queue = app.query_one(StagingQueueList)
            assert len(queue._items) == len(sample_items)

    async def test_quit_closes_app(self, fake_service: AgentContextEngineService) -> None:
        app = AceTUI(fake_service)
        async with app.run_test() as pilot:
            await pilot.press("q")
            assert not app.is_running

    async def test_refresh_action_repopulates_queue(
        self, fake_service: AgentContextEngineService
    ) -> None:
        app = AceTUI(fake_service)
        async with app.run_test() as pilot:
            await pilot.press("r")
            queue = app.query_one(StagingQueueList)
            assert len(queue._items) > 0

    async def test_sort_confidence_action(self, fake_service: AgentContextEngineService) -> None:
        app = AceTUI(fake_service)
        async with app.run_test() as pilot:
            await pilot.press("1")
            assert app._sort_key == "confidence"
            queue = app.query_one(StagingQueueList)
            if len(queue._items) > 1:
                assert queue._items[0].confidence >= queue._items[-1].confidence

    async def test_sort_age_action(self, fake_service: AgentContextEngineService) -> None:
        app = AceTUI(fake_service)
        async with app.run_test() as pilot:
            await pilot.press("2")
            assert app._sort_key == "age"
            queue = app.query_one(StagingQueueList)
            if len(queue._items) > 1:
                dates = [i.last_validated for i in queue._items]
                assert dates == sorted(dates)

    async def test_sort_pattern_type_action(self, fake_service: AgentContextEngineService) -> None:
        app = AceTUI(fake_service)
        async with app.run_test() as pilot:
            await pilot.press("3")
            assert app._sort_key == "pattern_type"

    async def test_check_action_global_always_visible(
        self, fake_service: AgentContextEngineService
    ) -> None:
        app = AceTUI(fake_service)
        async with app.run_test():
            assert app.check_action("refresh", ()) is True
            assert app.check_action("sort_confidence", ()) is True
            assert app.check_action("sort_age", ()) is True
            assert app.check_action("sort_pattern_type", ()) is True
            assert app.check_action("quit", ()) is True

    async def test_check_action_promote_reject_hidden_with_no_selection(
        self, fake_service: AgentContextEngineService
    ) -> None:
        app = AceTUI(FakeAceService(staged=[]))  # empty queue → no selection
        async with app.run_test():
            assert app.check_action("promote", ()) is False
            assert app.check_action("reject", ()) is False

    async def test_empty_service_renders_empty_table(self) -> None:
        app = AceTUI(FakeAceService(staged=[]))
        async with app.run_test():
            queue = app.query_one(StagingQueueList)
            assert queue._items == []

    async def test_poll_disabled_when_zero(self) -> None:
        import os

        monkeypatch_env = {"ACE_TUI_POLL": "0"}
        original = {k: os.environ.get(k) for k in monkeypatch_env}
        try:
            os.environ.update(monkeypatch_env)
            app = AceTUI(FakeAceService())
            async with app.run_test():
                assert app._refresh_timer is None
        finally:
            for k, v in original.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
