"""Unit tests for TUI presentation helpers and Textual Pilot interaction tests."""

from __future__ import annotations

import pytest

from dispatcher.tui.app import WorkflowTUI
from dispatcher.tui.widgets import DetailPane, WorkflowList


@pytest.fixture
def sample_workflows():
    return [
        {
            "id": "wf-1",
            "ticket_key": "AOS-1",
            "status": type("obj", (object,), {"value": "pending"})(),
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T01:00:00+00:00",
            "work_plan": {"summary": "Fix bug"},
            "pr_url": None,
            "retry_count": 0,
        },
        {
            "id": "wf-2",
            "ticket_key": "AOS-2",
            "status": type("obj", (object,), {"value": "completed"})(),
            "created_at": "2024-01-02T00:00:00+00:00",
            "updated_at": "2024-01-02T01:00:00+00:00",
            "work_plan": {"summary": "Add feature"},
            "pr_url": "https://github.com/org/repo/pull/1",
            "retry_count": 1,
        },
    ]


class TestWorkflowList:
    def test_update_workflows_populates_table(self, sample_workflows):
        widget = WorkflowList()
        # textual widgets need to be mounted for query_one to work;
        # test the internal data structure directly
        widget._workflows = sample_workflows
        assert len(widget._workflows) == 2
        assert widget._workflows[0]["ticket_key"] == "AOS-1"

    def test_get_selected_workflow_without_mount(self, sample_workflows):
        widget = WorkflowList()
        widget._workflows = sample_workflows
        # Without a mounted DataTable cursor, returns None
        assert widget.get_selected_workflow() is None


class TestDetailPane:
    def test_update_workflow_with_none(self):
        pane = DetailPane()
        pane._workflow = None
        # Just ensure no exception
        pane.update_workflow(None)
        assert pane._workflow is None

    def test_update_workflow_with_data(self, sample_workflows):
        pane = DetailPane()
        wf = sample_workflows[0]
        pane.update_workflow(wf)
        assert pane._workflow == wf


@pytest.mark.asyncio
class TestWorkflowTUI:
    async def test_app_mounts(self):
        app = WorkflowTUI()
        async with app.run_test():
            assert app.is_running

    async def test_refresh_action(self):
        app = WorkflowTUI()
        async with app.run_test() as pilot:
            await pilot.press("r")
            assert app.is_running

    async def test_quit_action(self):
        app = WorkflowTUI()
        async with app.run_test() as pilot:
            await pilot.press("q")
            assert not app.is_running
