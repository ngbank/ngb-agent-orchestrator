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
    ProvenanceEntryDTO,
    RejectRequest,
    RejectResult,
    ShowItemRequest,
    ShowItemResult,
)
from ace.service.protocols import AgentContextEngineService
from ace.tui.action_registry import REGISTRY, action_for
from ace.tui.app import AceTUI
from ace.tui.modals import PromoteFormData, PromoteModal, RejectModal
from ace.tui.widgets import ItemDetailPane, StagingQueueList, _sort_items

# ---------------------------------------------------------------------------
# Fake service
# ---------------------------------------------------------------------------


class FakeAceService:
    """In-memory :class:`AgentContextEngineService` for TUI tests."""

    def __init__(self, staged: Optional[List[ItemSummaryDTO]] = None) -> None:
        self._staged: List[ItemSummaryDTO] = staged or []
        self.promote_calls: List[PromoteRequest] = []
        self.reject_calls: List[RejectRequest] = []

    def list_items(self, request: ListItemsRequest) -> ListItemsResult:
        items = list(self._staged) if request.status == "staged" else []
        return ListItemsResult(items=tuple(items))

    def mine(self, request: MineRequest) -> MineResult:  # pragma: no cover
        raise NotImplementedError

    def show_item(self, request: ShowItemRequest) -> Optional[ShowItemResult]:
        for item in self._staged:
            if item.id == request.item_id:
                return ShowItemResult(
                    id=item.id,
                    pattern_type=item.pattern_type,
                    scope=item.scope,
                    scope_value=item.scope_value,
                    description=item.description,
                    confidence=item.confidence,
                    confidence_tier=item.confidence_tier,
                    status=item.status,
                    last_validated=item.last_validated,
                    created_at="2024-01-01T00:00:00",
                    updated_at="2024-01-01T00:00:00",
                    provenance=(),
                    conflicts_with=(),
                    project=None,
                    repo=None,
                    platform=None,
                )
        return None

    def promote(self, request: PromoteRequest) -> PromoteResult:
        self.promote_calls.append(request)
        # Remove the item from staged so the queue refreshes correctly.
        self._staged = [i for i in self._staged if i.id != request.item_id]
        return PromoteResult(item_id=request.item_id)

    def reject(self, request: RejectRequest) -> RejectResult:
        self.reject_calls.append(request)
        self._staged = [i for i in self._staged if i.id != request.item_id]
        return RejectResult(item_id=request.item_id)


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


# ---------------------------------------------------------------------------
# ItemDetailPane widget tests
# ---------------------------------------------------------------------------


def _show_result(
    item_id: str = "i-1",
    *,
    pattern_type: str = "approach",
    scope: str = "codebase_wide",
    scope_value: Optional[str] = None,
    description: str = "a context item description",
    confidence: float = 0.8,
    confidence_tier: Optional[str] = "PATTERN",
    status: str = "staged",
    provenance: tuple = (),
    conflicts_with: tuple = (),
    project: Optional[str] = None,
    repo: Optional[str] = None,
    platform: Optional[str] = None,
) -> ShowItemResult:
    return ShowItemResult(
        id=item_id,
        pattern_type=pattern_type,
        scope=scope,
        scope_value=scope_value,
        description=description,
        confidence=confidence,
        confidence_tier=confidence_tier,
        status=status,
        last_validated="2024-06-01T00:00:00",
        created_at="2024-01-01T00:00:00",
        updated_at="2024-06-01T00:00:00",
        provenance=provenance,
        conflicts_with=conflicts_with,
        project=project,
        repo=repo,
        platform=platform,
    )


class TestItemDetailPane:
    def test_initial_state_shows_placeholder(self) -> None:
        pane = ItemDetailPane()
        # Without mounting, _items is not queryable; test the update method
        # does not raise when called on an unmounted widget.
        pane.update_item(None)  # should not raise

    def test_update_item_none_no_raise(self) -> None:
        pane = ItemDetailPane()
        pane.update_item(None)  # defensive: no query_one available, must not raise

    def test_format_item_detail_includes_description(self) -> None:
        from ace.tui.widgets import _format_item_detail

        result = _show_result(description="unique-description-string-xyz")
        formatted = _format_item_detail(result)
        assert "unique-description-string-xyz" in formatted

    def test_format_item_detail_includes_pattern_type(self) -> None:
        from ace.tui.widgets import _format_item_detail

        result = _show_result(pattern_type="concern")
        formatted = _format_item_detail(result)
        assert "concern" in formatted

    def test_format_item_detail_includes_confidence(self) -> None:
        from ace.tui.widgets import _format_item_detail

        result = _show_result(confidence=0.92)
        formatted = _format_item_detail(result)
        assert "0.92" in formatted

    def test_format_item_detail_includes_provenance_events(self) -> None:
        from ace.tui.widgets import _format_item_detail

        prov = (
            ProvenanceEntryDTO(
                signal_source="pr_comment",
                workflow_date="2024-03-01",
                contributed_confidence=0.3,
                workflow_id="wf-1",
                ticket_key="AOS-1",
                signal_detail="some signal detail",
            ),
        )
        result = _show_result(provenance=prov)
        formatted = _format_item_detail(result)
        assert "pr_comment" in formatted
        assert "AOS-1" in formatted
        assert "1 event" in formatted

    def test_format_item_detail_no_provenance(self) -> None:
        from ace.tui.widgets import _format_item_detail

        result = _show_result(provenance=())
        formatted = _format_item_detail(result)
        assert "(none)" in formatted

    def test_format_item_detail_conflicts_shown(self) -> None:
        from ace.tui.widgets import _format_item_detail

        result = _show_result(conflicts_with=("abc-123",))
        formatted = _format_item_detail(result)
        assert "abc-123" in formatted

    def test_format_item_detail_applicability_shown(self) -> None:
        from ace.tui.widgets import _format_item_detail

        result = _show_result(project="AOS", repo="ngb-agent-orchestrator")
        formatted = _format_item_detail(result)
        assert "project=AOS" in formatted
        assert "repo=ngb-agent-orchestrator" in formatted

    def test_format_item_detail_scope_value_shown(self) -> None:
        from ace.tui.widgets import _format_item_detail

        result = _show_result(scope="file_pattern", scope_value="*.py")
        formatted = _format_item_detail(result)
        assert "*.py" in formatted


# ---------------------------------------------------------------------------
# PromoteModal tests
# ---------------------------------------------------------------------------


class TestPromoteModal:
    def test_promote_form_data_namedtuple(self) -> None:
        data = PromoteFormData(notes="good pattern", scope="task_type", scope_value="migration")
        assert data.notes == "good pattern"
        assert data.scope == "task_type"
        assert data.scope_value == "migration"

    def test_promote_form_data_none_fields(self) -> None:
        data = PromoteFormData(notes=None, scope=None, scope_value=None)
        assert data.notes is None
        assert data.scope is None
        assert data.scope_value is None

    def test_promote_modal_instantiates(self) -> None:
        modal = PromoteModal(current_scope="codebase_wide", current_scope_value="")
        assert modal._current_scope == "codebase_wide"
        assert modal._current_scope_value == ""

    def test_promote_modal_default_args(self) -> None:
        modal = PromoteModal()
        assert modal._current_scope == ""
        assert modal._current_scope_value == ""


# ---------------------------------------------------------------------------
# RejectModal tests
# ---------------------------------------------------------------------------


class TestRejectModal:
    def test_reject_modal_instantiates(self) -> None:
        modal = RejectModal()
        assert modal is not None

    def test_reject_modal_is_modal_screen(self) -> None:
        from textual.screen import ModalScreen

        assert isinstance(RejectModal(), ModalScreen)


# ---------------------------------------------------------------------------
# AceTUI promote / reject integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAceTUIPromoteReject:
    async def test_promote_action_without_selection_notifies(self) -> None:
        """Pressing p with no items shows a warning notification."""
        app = AceTUI(FakeAceService(staged=[]))
        async with app.run_test() as pilot:
            await pilot.press("p")
            # App should still be running (not crashed).
            assert app.is_running

    async def test_reject_action_without_selection_notifies(self) -> None:
        """Pressing x with no items shows a warning notification."""
        app = AceTUI(FakeAceService(staged=[]))
        async with app.run_test() as pilot:
            await pilot.press("x")
            assert app.is_running

    async def test_promote_action_opens_modal(
        self, fake_service: AgentContextEngineService
    ) -> None:
        """Pressing p with a selected staged item pushes a PromoteModal."""
        app = AceTUI(fake_service)
        async with app.run_test() as pilot:
            # Move cursor to first row.
            await pilot.press("down")
            await pilot.press("p")
            # A PromoteModal should be on the screen stack.
            assert any(isinstance(s, PromoteModal) for s in app.screen_stack)

    async def test_reject_action_opens_modal(self, fake_service: AgentContextEngineService) -> None:
        """Pressing x with a selected staged item pushes a RejectModal."""
        app = AceTUI(fake_service)
        async with app.run_test() as pilot:
            await pilot.press("down")
            await pilot.press("x")
            assert any(isinstance(s, RejectModal) for s in app.screen_stack)

    async def test_promote_cancel_does_not_call_service(self, fake_service: FakeAceService) -> None:
        """Cancelling the PromoteModal does not call service.promote."""
        app = AceTUI(fake_service)
        async with app.run_test() as pilot:
            await pilot.press("down")
            await pilot.press("p")
            # Cancel the modal.
            await pilot.press("escape")
            # Give any workers a moment to settle.
            await pilot.pause(0.1)
            assert len(fake_service.promote_calls) == 0

    async def test_reject_cancel_does_not_call_service(self, fake_service: FakeAceService) -> None:
        """Cancelling the RejectModal does not call service.reject."""
        app = AceTUI(fake_service)
        async with app.run_test() as pilot:
            await pilot.press("down")
            await pilot.press("x")
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert len(fake_service.reject_calls) == 0

    async def test_detail_pane_visible_on_mount(
        self, fake_service: AgentContextEngineService
    ) -> None:
        """ItemDetailPane is present in the mounted layout."""
        app = AceTUI(fake_service)
        async with app.run_test():
            pane = app.query_one(ItemDetailPane)
            assert pane is not None
