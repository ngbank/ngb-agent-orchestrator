"""Integration smoke test launching the TUI against a service-backed store.

Seeds the real SQLite repository and then wires it into a
``LocalWorkflowService`` so the TUI exercises the full service path end to
end, without reaching into the repository directly from TUI code.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from dispatcher.tui.app import WorkflowTUI
from dispatcher.tui.widgets import DetailPane, WorkflowList
from orchestrator.workflow_service import build_local_workflow_service
from state import workflow_repository as state_store
from state.workflow_status import WorkflowStatus


@pytest.fixture
def seeded_db():
    """Create a temporary database seeded with workflows for TUI testing."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = f.name
    os.unlink(db_path)

    original_db_path = os.environ.get("DB_PATH")
    os.environ["DB_PATH"] = db_path
    state_store.run_migrations()

    state_store.create_workflow(
        "AOS-100",
        work_plan={"summary": "Add TUI support"},
        status=WorkflowStatus.PENDING_APPROVAL,
    )
    state_store.create_workflow(
        "AOS-101",
        work_plan={"summary": "Fix bug in retry"},
        status=WorkflowStatus.FAILED,
    )
    state_store.create_workflow(
        "AOS-102",
        work_plan={"summary": "Update docs"},
        status=WorkflowStatus.COMPLETED,
    )

    yield db_path

    if os.path.exists(db_path):
        os.unlink(db_path)
    if original_db_path:
        os.environ["DB_PATH"] = original_db_path
    elif "DB_PATH" in os.environ:
        del os.environ["DB_PATH"]


def _service():
    """Build a service that talks to the env-pointed SQLite DB.

    A no-op graph factory keeps the langgraph builder out of the integration
    test; none of the TUI read paths exercised here need the graph.
    """
    return build_local_workflow_service(graph_factory=lambda: None)


@pytest.mark.asyncio
class TestTUIIntegration:
    async def test_tui_lists_workflows(self, seeded_db):
        app = WorkflowTUI(_service())
        async with app.run_test():
            workflow_list = app.query_one(WorkflowList)
            assert len(workflow_list._workflows) == 3
            tickets = {w.ticket_key for w in workflow_list._workflows}
            assert tickets == {"AOS-100", "AOS-101", "AOS-102"}

    async def test_tui_detail_pane_updates_on_selection(self, seeded_db):
        app = WorkflowTUI(_service())
        async with app.run_test():
            workflow_list = app.query_one(WorkflowList)
            detail = app.query_one(DetailPane)
            # The most recently created workflow is first; verify the detail
            # pane was populated with that workflow's data via the service.
            assert workflow_list._workflows
            first = workflow_list._workflows[0]
            assert detail._workflow is not None
            assert detail._workflow.ticket_key == first.ticket_key
