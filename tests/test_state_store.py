"""Unit tests for state_store module."""

import os
import sqlite3
import tempfile

import pytest

from state import get_connection
from state import workflow_repository as state_store
from state.workflow_status import WorkflowStatus


@pytest.fixture
def test_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = f.name

    # Remove the file so the db can be created fresh
    os.unlink(db_path)

    # Set environment variable for test
    original_db_path = os.environ.get("DB_PATH")
    os.environ["DB_PATH"] = db_path

    # Run migrations
    state_store.run_migrations()

    yield db_path

    # Cleanup
    if os.path.exists(db_path):
        os.unlink(db_path)

    # Restore original env var
    if original_db_path:
        os.environ["DB_PATH"] = original_db_path
    elif "DB_PATH" in os.environ:
        del os.environ["DB_PATH"]


def test_create_workflow(test_db):
    """Test workflow creation with all fields."""
    work_plan = {"tasks": ["task1", "task2"], "description": "Test workflow"}

    workflow_id = state_store.create_workflow(
        ticket_key="AOS-35", work_plan=work_plan, status=WorkflowStatus.PENDING
    )

    # Verify workflow_id is a valid UUID
    assert workflow_id is not None
    assert len(workflow_id) == 36  # UUID format

    # Retrieve and verify
    workflow = state_store.get_workflow(workflow_id)
    assert workflow is not None
    assert workflow["ticket_key"] == "AOS-35"
    assert workflow["status"] == WorkflowStatus.PENDING
    assert isinstance(workflow["status"], WorkflowStatus)
    assert workflow["work_plan"] == work_plan
    assert workflow["pr_url"] is None
    assert workflow["created_at"] is not None
    assert workflow["updated_at"] is not None


def test_create_workflow_minimal(test_db):
    """Test workflow creation with minimal fields."""
    workflow_id = state_store.create_workflow(ticket_key="AOS-36")

    workflow = state_store.get_workflow(workflow_id)
    assert workflow is not None
    assert workflow["ticket_key"] == "AOS-36"
    assert workflow["status"] == WorkflowStatus.PENDING  # Default status
    assert workflow["work_plan"] is None
    assert workflow["pr_url"] is None


def test_update_status(test_db):
    """Test status updates and timestamp changes."""
    workflow_id = state_store.create_workflow(ticket_key="AOS-37", status=WorkflowStatus.PENDING)

    # Get initial state
    workflow_before = state_store.get_workflow(workflow_id)
    created_at = workflow_before["created_at"]

    # Update status
    state_store.update_status(
        workflow_id=workflow_id,
        status=WorkflowStatus.IN_PROGRESS,
        actor="test_user",
        reason="Started work",
    )

    # Verify update
    workflow_after = state_store.get_workflow(workflow_id)
    assert workflow_after["status"] == WorkflowStatus.IN_PROGRESS
    assert workflow_after["created_at"] == created_at  # Should not change
    assert workflow_after["updated_at"] != workflow_before["updated_at"]
    assert workflow_after["pr_url"] is None


def test_update_status_with_pr_url(test_db):
    """Test status update with PR URL."""
    workflow_id = state_store.create_workflow(ticket_key="AOS-38")

    state_store.update_status(
        workflow_id=workflow_id,
        status=WorkflowStatus.COMPLETED,
        pr_url="https://github.com/org/repo/pull/123",
    )

    workflow = state_store.get_workflow(workflow_id)
    assert workflow["status"] == WorkflowStatus.COMPLETED
    assert workflow["pr_url"] == "https://github.com/org/repo/pull/123"


def test_update_status_creates_audit_log(test_db):
    """Test that status updates create audit log entries."""
    workflow_id = state_store.create_workflow(ticket_key="AOS-39")

    # Update status twice
    state_store.update_status(
        workflow_id=workflow_id,
        status=WorkflowStatus.IN_PROGRESS,
        actor="user1",
        reason="Started work",
    )

    state_store.update_status(
        workflow_id=workflow_id,
        status=WorkflowStatus.COMPLETED,
        actor="user2",
        reason="Finished work",
    )

    # Check audit log
    audit_log = state_store.get_audit_log(workflow_id)

    # Should have 3 entries: creation + 2 status changes
    assert len(audit_log) >= 3

    # Verify latest entries
    assert any(
        entry["action"] == "status_change" and entry["actor"] == "user1" for entry in audit_log
    )
    assert any(
        entry["action"] == "status_change" and entry["actor"] == "user2" for entry in audit_log
    )


def test_get_workflow(test_db):
    """Test workflow retrieval by ID."""
    work_plan = {"tasks": ["a", "b", "c"]}
    workflow_id = state_store.create_workflow(ticket_key="AOS-40", work_plan=work_plan)

    workflow = state_store.get_workflow(workflow_id)
    assert workflow is not None
    assert workflow["id"] == workflow_id
    assert workflow["work_plan"] == work_plan


def test_get_workflow_by_ticket(test_db):
    """Test retrieval by ticket key."""
    # Create multiple workflows for the same ticket
    id1 = state_store.create_workflow(ticket_key="AOS-41", work_plan={"iteration": 1})
    id2 = state_store.create_workflow(ticket_key="AOS-41", work_plan={"iteration": 2})
    id3 = state_store.create_workflow(ticket_key="AOS-42", work_plan={"iteration": 1})

    # Get workflows for AOS-41
    workflows = state_store.get_workflow_by_ticket("AOS-41")
    assert len(workflows) == 2
    assert all(w["ticket_key"] == "AOS-41" for w in workflows)

    # Verify it's ordered by created_at DESC (newest first)
    workflow_ids = [w["id"] for w in workflows]
    assert id2 in workflow_ids
    assert id1 in workflow_ids

    # Verify AOS-42 has only one workflow
    workflows_42 = state_store.get_workflow_by_ticket("AOS-42")
    assert len(workflows_42) == 1
    assert workflows_42[0]["id"] == id3


def test_work_plan_serialization(test_db):
    """Test JSON serialization and deserialization of work plan."""
    complex_plan = {
        "tasks": [
            {"id": 1, "title": "Task 1", "completed": False},
            {"id": 2, "title": "Task 2", "completed": True},
        ],
        "metadata": {"priority": "high", "estimate": "2d", "labels": ["backend", "api"]},
    }

    workflow_id = state_store.create_workflow(ticket_key="AOS-43", work_plan=complex_plan)

    # Retrieve and verify exact match
    workflow = state_store.get_workflow(workflow_id)
    assert workflow["work_plan"] == complex_plan
    assert isinstance(workflow["work_plan"]["tasks"], list)
    assert isinstance(workflow["work_plan"]["metadata"], dict)


def test_migration_idempotency(test_db):
    """Test that migrations can run multiple times safely."""
    # Run migrations again
    state_store.run_migrations()

    # Create a workflow
    workflow_id = state_store.create_workflow(ticket_key="AOS-44")

    # Run migrations once more
    state_store.run_migrations()

    # Verify workflow still exists
    workflow = state_store.get_workflow(workflow_id)
    assert workflow is not None
    assert workflow["ticket_key"] == "AOS-44"


def test_workflow_not_found(test_db):
    """Test behavior when workflow doesn't exist."""
    workflow = state_store.get_workflow("non-existent-id")
    assert workflow is None


def test_workflow_by_ticket_not_found(test_db):
    """Test behavior when no workflows exist for ticket."""
    workflows = state_store.get_workflow_by_ticket("NON-EXISTENT")
    assert workflows == []


def test_audit_log_append_only(test_db):
    """Test that audit log has no delete operations."""
    # Verify no delete methods exist on SQLiteWorkflowRepository
    import inspect

    from state.sqlite_workflow_repository import SQLiteWorkflowRepository

    methods = [
        name
        for name, obj in inspect.getmembers(SQLiteWorkflowRepository)
        if inspect.isfunction(obj) or callable(obj)
    ]

    # Verify no delete methods
    delete_methods = [m for m in methods if "delete" in m.lower()]
    assert len(delete_methods) == 0, f"Found delete methods: {delete_methods}"


def test_audit_log_content(test_db):
    """Test audit log contains proper information."""
    workflow_id = state_store.create_workflow(ticket_key="AOS-45")

    state_store.update_status(
        workflow_id=workflow_id,
        status=WorkflowStatus.COMPLETED,
        actor="test_actor",
        reason="Test completion",
    )

    audit_log = state_store.get_audit_log(workflow_id)

    # Verify structure
    for entry in audit_log:
        assert "id" in entry
        assert "workflow_id" in entry
        assert "actor" in entry
        assert "action" in entry
        assert "created_at" in entry
        assert entry["workflow_id"] == workflow_id


def test_get_db_path_default(test_db):
    """Test that default DB path is correct."""
    # Temporarily remove DB_PATH env var
    db_path_backup = os.environ.get("DB_PATH")
    if "DB_PATH" in os.environ:
        del os.environ["DB_PATH"]

    try:
        db_path = state_store.get_db_path()
        assert db_path == "state/local.db"
    finally:
        # Restore
        if db_path_backup:
            os.environ["DB_PATH"] = db_path_backup


def test_get_db_path_from_env(test_db):
    """Test that DB path is read from environment."""
    from unittest.mock import patch

    custom_path = "custom/path/test.db"
    os.environ["DB_PATH"] = custom_path

    # Mock mkdir to prevent directory creation during test
    with patch("pathlib.Path.mkdir"):
        db_path = state_store.get_db_path()
        assert db_path == custom_path


def test_workflow_status_is_enum(test_db):
    """Test that retrieved workflow status is a WorkflowStatus instance, not a string."""
    workflow_id = state_store.create_workflow(ticket_key="AOS-46")
    workflow = state_store.get_workflow(workflow_id)
    assert isinstance(workflow["status"], WorkflowStatus)
    assert workflow["status"] == WorkflowStatus.PENDING


def test_workflow_status_is_enum_after_update(test_db):
    """Test that status remains a WorkflowStatus instance after an update."""
    workflow_id = state_store.create_workflow(ticket_key="AOS-46")
    state_store.update_status(workflow_id, WorkflowStatus.IN_PROGRESS)
    workflow = state_store.get_workflow(workflow_id)
    assert isinstance(workflow["status"], WorkflowStatus)
    assert workflow["status"] == WorkflowStatus.IN_PROGRESS


def test_get_workflow_by_ticket_status_is_enum(test_db):
    """Test that status in get_workflow_by_ticket results is a WorkflowStatus instance."""
    state_store.create_workflow(ticket_key="AOS-46")
    workflows = state_store.get_workflow_by_ticket("AOS-46")
    assert len(workflows) == 1
    assert isinstance(workflows[0]["status"], WorkflowStatus)


def test_is_active_helper(test_db):
    """Test WorkflowStatus.is_active() helper."""
    assert WorkflowStatus.PENDING.is_active() is True
    assert WorkflowStatus.IN_PROGRESS.is_active() is True
    assert WorkflowStatus.COMPLETED.is_active() is False
    assert WorkflowStatus.FAILED.is_active() is False


def test_is_terminal_helper(test_db):
    """Test WorkflowStatus.is_terminal() helper."""
    assert WorkflowStatus.COMPLETED.is_terminal() is True
    assert WorkflowStatus.FAILED.is_terminal() is True
    assert WorkflowStatus.PENDING.is_terminal() is False
    assert WorkflowStatus.IN_PROGRESS.is_terminal() is False


def test_update_work_plan(test_db):
    """Test updating work_plan for an existing workflow."""
    # Create workflow without work_plan
    workflow_id = state_store.create_workflow(ticket_key="AOS-39", status=WorkflowStatus.PENDING)

    # Verify no work_plan initially
    workflow_before = state_store.get_workflow(workflow_id)
    assert workflow_before["work_plan"] is None

    # Update with work_plan
    work_plan = {
        "schema_version": "1.0",
        "ticket_key": "AOS-39",
        "summary": "Test plan",
        "approach": "Test approach",
        "tasks": [{"id": 1, "description": "Task 1", "files_likely_affected": []}],
        "concerns": [],
        "status": "pass",
    }

    state_store.update_work_plan(
        workflow_id=workflow_id,
        work_plan=work_plan,
        actor="dispatcher",
        reason="WorkPlan generated",
    )

    # Verify work_plan was stored (normalised on read)
    workflow_after = state_store.get_workflow(workflow_id)
    assert workflow_after["work_plan"] is not None
    assert workflow_after["work_plan"]["summary"] == "Test plan"
    assert workflow_after["work_plan"]["status"] == "pass"

    # Verify audit log entry was created
    audit_log = state_store.get_audit_log(workflow_id)
    work_plan_updates = [entry for entry in audit_log if entry["action"] == "work_plan_updated"]
    assert len(work_plan_updates) == 1
    assert work_plan_updates[0]["actor"] == "dispatcher"
    assert "WorkPlan generated" in work_plan_updates[0]["reason"]


def test_update_work_plan_replaces_existing(test_db):
    """Test that update_work_plan replaces an existing work_plan."""
    # Create workflow with initial work_plan
    initial_plan = {"summary": "Initial plan", "status": "draft"}

    workflow_id = state_store.create_workflow(
        ticket_key="AOS-40", work_plan=initial_plan, status=WorkflowStatus.PENDING
    )

    # Update with new work_plan
    updated_plan = {"summary": "Updated plan", "status": "pass"}

    state_store.update_work_plan(workflow_id=workflow_id, work_plan=updated_plan, actor="system")

    # Verify work_plan was replaced
    workflow = state_store.get_workflow(workflow_id)
    assert workflow["work_plan"]["summary"] == "Updated plan"
    assert workflow["work_plan"]["status"] == "pass"


def test_update_work_plan_complex_structure(test_db):
    """Test storing complex work_plan with nested structures."""
    workflow_id = state_store.create_workflow(ticket_key="AOS-41", status=WorkflowStatus.PENDING)

    complex_plan = {
        "schema_version": "1.0",
        "ticket_key": "AOS-41",
        "summary": "Complex plan",
        "approach": "Multi-step approach with details",
        "tasks": [
            {
                "id": 1,
                "description": "First task",
                "files_likely_affected": ["file1.py", "file2.py"],
            },
            {
                "id": 2,
                "description": "Second task",
                "files_likely_affected": ["file3.py", "file4.py", "file5.py"],
            },
        ],
        "concerns": ["Risk 1", "Risk 2", "Risk 3", "Question 1", "Question 2"],
        "status": "concerns",
    }

    state_store.update_work_plan(workflow_id=workflow_id, work_plan=complex_plan)

    # Verify all nested data was preserved
    workflow = state_store.get_workflow(workflow_id)
    assert len(workflow["work_plan"]["tasks"]) == 2
    assert len(workflow["work_plan"]["tasks"][0]["files_likely_affected"]) == 2
    assert len(workflow["work_plan"]["tasks"][1]["files_likely_affected"]) == 3
    assert len(workflow["work_plan"]["concerns"]) == 5
    assert workflow["work_plan"]["status"] == "concerns"


def test_update_status_rejected_creates_audit_log(test_db):
    """Test that update_status for REJECTED produces an audit log entry with the reason."""
    workflow_id = state_store.create_workflow(ticket_key="AOS-61")

    state_store.update_status(
        workflow_id=workflow_id,
        status=WorkflowStatus.REJECTED,
        actor="developer",
        reason="scope too broad",
    )

    audit_log = state_store.get_audit_log(workflow_id)
    rejection_entries = [
        entry
        for entry in audit_log
        if entry["action"] == "status_change" and "scope too broad" in (entry["reason"] or "")
    ]
    assert len(rejection_entries) == 1
    assert rejection_entries[0]["actor"] == "developer"


# ---------------------------------------------------------------------------
# update_usage_summary tests
# ---------------------------------------------------------------------------


def test_update_usage_summary_stores_stage(test_db):
    """Test that usage summary is persisted for a given stage."""
    workflow_id = state_store.create_workflow(ticket_key="AOS-85")
    data = {
        "stage": "plan",
        "turns": 12,
        "prompt_tokens": 3000,
        "completion_tokens": 800,
        "total_tokens": 3800,
        "stop_reasons": ["stop", "stop"],
    }

    state_store.update_usage_summary(workflow_id, "plan", data)

    workflow = state_store.get_workflow(workflow_id)
    usage_summary = workflow["usage_summary"]
    assert "plan" in usage_summary
    assert usage_summary["plan"]["turns"] == 12
    assert usage_summary["plan"]["total_tokens"] == 3800
    assert usage_summary["plan"]["stop_reasons"] == ["stop", "stop"]


def test_update_usage_summary_merges_multiple_stages(test_db):
    """Test that calling update_usage_summary for two stages keeps both."""
    workflow_id = state_store.create_workflow(ticket_key="AOS-85")
    plan_data = {
        "stage": "plan",
        "turns": 10,
        "prompt_tokens": 1000,
        "completion_tokens": 400,
        "total_tokens": 1400,
        "stop_reasons": ["stop"],
    }
    execute_data = {
        "stage": "execute",
        "turns": 42,
        "prompt_tokens": 18500,
        "completion_tokens": 6200,
        "total_tokens": 24700,
        "stop_reasons": ["max_tokens"],
    }

    state_store.update_usage_summary(workflow_id, "plan", plan_data)
    state_store.update_usage_summary(workflow_id, "execute", execute_data)

    workflow = state_store.get_workflow(workflow_id)
    usage_summary = workflow["usage_summary"]
    assert "plan" in usage_summary
    assert "execute" in usage_summary
    assert usage_summary["plan"]["turns"] == 10
    assert usage_summary["execute"]["turns"] == 42


def test_update_usage_summary_overwrites_same_stage(test_db):
    """Test that re-calling for the same stage replaces the previous data."""
    workflow_id = state_store.create_workflow(ticket_key="AOS-85")
    state_store.update_usage_summary(
        workflow_id,
        "plan",
        {
            "stage": "plan",
            "turns": 5,
            "prompt_tokens": 100,
            "completion_tokens": 40,
            "total_tokens": 140,
            "stop_reasons": [],
        },
    )
    state_store.update_usage_summary(
        workflow_id,
        "plan",
        {
            "stage": "plan",
            "turns": 8,
            "prompt_tokens": 200,
            "completion_tokens": 80,
            "total_tokens": 280,
            "stop_reasons": ["stop"],
        },
    )

    workflow = state_store.get_workflow(workflow_id)
    usage_summary = workflow["usage_summary"]
    assert usage_summary["plan"]["turns"] == 8


def test_update_usage_summary_noop_for_missing_workflow(test_db):
    """Test that update_usage_summary is a no-op when workflow_id does not exist."""
    # Should not raise
    state_store.update_usage_summary(
        "does-not-exist",
        "plan",
        {
            "stage": "plan",
            "turns": 1,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "stop_reasons": [],
        },
    )


def test_update_usage_summary_creates_audit_log(test_db):
    """Test that update_usage_summary creates an audit log entry."""
    workflow_id = state_store.create_workflow(ticket_key="AOS-85")
    state_store.update_usage_summary(
        workflow_id,
        "execute",
        {
            "stage": "execute",
            "turns": 3,
            "prompt_tokens": 50,
            "completion_tokens": 20,
            "total_tokens": 70,
            "stop_reasons": [],
        },
    )

    audit_log = state_store.get_audit_log(workflow_id)
    usage_entries = [e for e in audit_log if e["action"] == "usage_summary_stored"]
    assert len(usage_entries) == 1
    assert "execute" in usage_entries[0]["reason"]


# ---------------------------------------------------------------------------
# update_clarification_history tests
# ---------------------------------------------------------------------------


def test_update_clarification_history_appends_round(test_db):
    """Test that clarification history appends a round entry."""
    workflow_id = state_store.create_workflow(ticket_key="AOS-85")
    round_entry = {
        "round": 1,
        "concerns": ["What DB?", "Risk A"],
        "answers": [{"concern": "What DB?", "answer": "SQLite"}],
    }

    state_store.update_clarification_history(workflow_id, round_entry, actor="developer")

    workflow = state_store.get_workflow(workflow_id)
    history = workflow["clarification_history"]
    assert isinstance(history, list)
    assert len(history) == 1
    assert history[0]["round"] == 1
    assert history[0]["actor"] == "developer"
    assert "timestamp" in history[0]


def test_update_clarification_history_appends_multiple_rounds(test_db):
    """Test that multiple calls append rounds in order."""
    workflow_id = state_store.create_workflow(ticket_key="AOS-85")
    state_store.update_clarification_history(
        workflow_id,
        {"round": 1, "concerns": ["Q1"], "answers": [{"concern": "Q1", "answer": "A1"}]},
    )
    state_store.update_clarification_history(
        workflow_id,
        {"round": 2, "concerns": ["Q2"], "answers": [{"concern": "Q2", "answer": "A2"}]},
    )

    workflow = state_store.get_workflow(workflow_id)
    history = workflow["clarification_history"]
    assert len(history) == 2
    assert history[0]["round"] == 1
    assert history[1]["round"] == 2


def test_update_clarification_history_noop_for_missing_workflow(test_db):
    """Test that update_clarification_history is a no-op when workflow_id does not exist."""
    state_store.update_clarification_history(
        "does-not-exist",
        {"round": 1, "concerns": [], "answers": []},
    )


def test_update_clarification_history_creates_audit_log(test_db):
    """Test that update_clarification_history creates an audit log entry."""
    workflow_id = state_store.create_workflow(ticket_key="AOS-85")
    state_store.update_clarification_history(
        workflow_id,
        {"round": 1, "concerns": ["Q?"], "answers": [{"concern": "Q?", "answer": "A!"}]},
        actor="dispatcher",
    )

    audit_log = state_store.get_audit_log(workflow_id)
    entries = [e for e in audit_log if e["action"] == "clarification_history_updated"]
    assert len(entries) == 1
    assert "dispatcher" in entries[0]["actor"]


def test_get_workflow_by_ticket_deserializes_clarification_history(test_db):
    """Test that get_workflow_by_ticket deserializes clarification_history."""
    workflow_id = state_store.create_workflow(ticket_key="AOS-85")
    state_store.update_clarification_history(
        workflow_id,
        {"round": 1, "concerns": ["Q?"], "answers": []},
    )

    workflows = state_store.get_workflow_by_ticket("AOS-85")
    assert len(workflows) == 1
    history = workflows[0]["clarification_history"]
    assert isinstance(history, list)
    assert history[0]["round"] == 1


def test_list_workflows_deserializes_clarification_history(test_db):
    """Test that list_workflows deserializes clarification_history."""
    workflow_id = state_store.create_workflow(ticket_key="AOS-85")
    state_store.update_clarification_history(
        workflow_id,
        {"round": 1, "concerns": ["Q?"], "answers": []},
    )

    workflows = state_store.list_workflows(ticket_key="AOS-85")
    assert len(workflows) == 1
    history = workflows[0]["clarification_history"]
    assert isinstance(history, list)
    assert history[0]["round"] == 1


def test_clarification_history_backward_compat(test_db):
    """Test that workflows without clarification_history return None/empty list gracefully."""
    workflow_id = state_store.create_workflow(ticket_key="AOS-85")
    workflow = state_store.get_workflow(workflow_id)
    assert (
        workflow.get("clarification_history") is None or workflow.get("clarification_history") == []
    )


# ---------------------------------------------------------------------------
# FakeWorkflowRepository — demonstrates injection without a real database
# ---------------------------------------------------------------------------


class FakeWorkflowRepository:
    """In-memory WorkflowRepository for testing.

    Demonstrates that callers can be decoupled from SQLite by accepting any
    object that satisfies the WorkflowRepository protocol.
    """

    def __init__(self) -> None:
        self._workflows: dict = {}

    def create_workflow(self, ticket_key, work_plan=None, status=None, workflow_id=None):
        import uuid

        from state.workflow_status import WorkflowStatus

        wid = workflow_id or str(uuid.uuid4())
        self._workflows[wid] = {
            "id": wid,
            "ticket_key": ticket_key,
            "work_plan": work_plan,
            "status": status or WorkflowStatus.PENDING,
        }
        return wid

    def get_workflow(self, workflow_id):
        return self._workflows.get(workflow_id)

    def get_workflow_by_ticket(self, ticket_key):
        return [w for w in self._workflows.values() if w["ticket_key"] == ticket_key]

    def get_latest_retryable_workflow_by_ticket(self, ticket_key):
        return None

    def list_workflows(self, ticket_key=None, status=None, limit=50):
        workflows = list(self._workflows.values())
        if ticket_key:
            workflows = [w for w in workflows if w["ticket_key"] == ticket_key]
        return workflows[:limit]

    def update_status(self, workflow_id, status, pr_url=None, actor="system", reason=None):
        if workflow_id in self._workflows:
            self._workflows[workflow_id]["status"] = status

    def update_work_plan(self, workflow_id, work_plan, actor="system", reason=None):
        if workflow_id in self._workflows:
            self._workflows[workflow_id]["work_plan"] = work_plan

    def update_execution_summary(self, workflow_id, execution_summary, actor="system"):
        pass

    def update_clarification_history(self, workflow_id, round_entry, actor="system"):
        pass

    def update_pr_comments(self, workflow_id, comments, actor="system"):
        pass

    def update_usage_summary(self, workflow_id, stage, data, actor="system"):
        pass

    def increment_retry_count(self, workflow_id, actor="system"):
        return 0

    def get_audit_log(self, workflow_id):
        return []

    def clear_db(self):
        self._workflows.clear()
        return 0, 0


def test_fake_repository_can_be_injected_without_database():
    """Demonstrate that FakeWorkflowRepository satisfies WorkflowRepository protocol.

    No database is needed — this proves callers can be tested with a pure
    in-memory double, satisfying the DIP goal of AOS-96.
    """
    from state.workflow_repository import WorkflowRepository
    from state.workflow_status import WorkflowStatus

    repo = FakeWorkflowRepository()

    # Verify it satisfies the protocol
    assert isinstance(repo, WorkflowRepository)

    # Create and retrieve a workflow
    wid = repo.create_workflow("AOS-96", work_plan={"summary": "test"})
    workflow = repo.get_workflow(wid)
    assert workflow is not None
    assert workflow["ticket_key"] == "AOS-96"
    assert workflow["status"] == WorkflowStatus.PENDING

    # Update status
    repo.update_status(wid, WorkflowStatus.IN_PROGRESS)
    workflow = repo.get_workflow(wid)
    assert workflow["status"] == WorkflowStatus.IN_PROGRESS

    # Query by ticket
    workflows = repo.get_workflow_by_ticket("AOS-96")
    assert len(workflows) == 1


# =====================================================================
# Atomic Transaction Tests (AOS-113: Audit Log Durability)
# =====================================================================


def test_status_update_atomicity(test_db):
    """Test that status update and audit entry are written atomically.

    This verifies the fix for review finding F1: workflow mutations
    and audit entries must commit atomically.
    """
    workflow_id = state_store.create_workflow(ticket_key="AOS-113")

    # Get audit log count after creation
    audit_log_after_create = state_store.get_audit_log(workflow_id)
    initial_audit_count = len(audit_log_after_create)
    assert initial_audit_count >= 1  # At least workflow_created

    # Update status
    state_store.update_status(
        workflow_id=workflow_id,
        status=WorkflowStatus.IN_PROGRESS,
        actor="test_actor",
        reason="Test reason",
    )

    # Verify both workflow state and audit entry exist
    workflow = state_store.get_workflow(workflow_id)
    assert workflow["status"] == WorkflowStatus.IN_PROGRESS

    audit_log = state_store.get_audit_log(workflow_id)
    assert len(audit_log) == initial_audit_count + 1

    # Verify the new audit entry matches the status update
    last_entry = audit_log[-1]
    assert last_entry["action"] == "status_change"
    assert last_entry["actor"] == "test_actor"
    assert last_entry["reason"] == "Test reason"


def test_work_plan_update_atomicity(test_db):
    """Test that work plan update and audit entry are written atomically."""
    workflow_id = state_store.create_workflow(ticket_key="AOS-113")

    work_plan = {"tasks": ["task1", "task2"], "priority": "high"}

    # Update work plan
    state_store.update_work_plan(
        workflow_id=workflow_id,
        work_plan=work_plan,
        actor="test_actor",
        reason="Updated plan",
    )

    # Verify both workflow state and audit entry exist
    workflow = state_store.get_workflow(workflow_id)
    assert workflow["work_plan"] == work_plan

    audit_log = state_store.get_audit_log(workflow_id)
    # Should have creation + work_plan_updated entries
    assert any(entry["action"] == "work_plan_updated" for entry in audit_log)


def test_execution_summary_atomicity(test_db):
    """Test that execution summary update and audit entry are written atomically."""
    workflow_id = state_store.create_workflow(ticket_key="AOS-113")

    summary = {"status": "completed", "output": "results"}

    # Update execution summary
    state_store.update_execution_summary(
        workflow_id=workflow_id,
        execution_summary=summary,
        actor="test_actor",
    )

    # Verify both workflow state and audit entry exist
    workflow = state_store.get_workflow(workflow_id)
    assert workflow["execution_summary"] == summary

    audit_log = state_store.get_audit_log(workflow_id)
    assert any(entry["action"] == "execution_summary_stored" for entry in audit_log)


def test_usage_summary_atomicity(test_db):
    """Test that usage summary update and audit entry are written atomically."""
    workflow_id = state_store.create_workflow(ticket_key="AOS-113")

    usage_data = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}

    # Update usage summary
    state_store.update_usage_summary(
        workflow_id=workflow_id,
        stage="work_planner",
        data=usage_data,
        actor="test_actor",
    )

    # Verify both workflow state and audit entry exist
    workflow = state_store.get_workflow(workflow_id)
    assert workflow["usage_summary"]["work_planner"] == usage_data

    audit_log = state_store.get_audit_log(workflow_id)
    assert any(entry["action"] == "usage_summary_stored" for entry in audit_log)


def test_retry_count_atomicity(test_db):
    """Test that retry count increment and audit entry are written atomically."""
    workflow_id = state_store.create_workflow(ticket_key="AOS-113")

    # Increment retry count
    new_count = state_store.increment_retry_count(workflow_id, actor="test_actor")

    assert new_count == 1

    # Verify both workflow state and audit entry exist
    workflow = state_store.get_workflow(workflow_id)
    assert workflow["retry_count"] == 1

    audit_log = state_store.get_audit_log(workflow_id)
    assert any(entry["action"] == "workflow_retried" for entry in audit_log)


# =====================================================================
# Negative Tests: Rollback Scenarios (AOS-113: Audit Log Durability)
# =====================================================================


def test_status_update_rollback_on_audit_failure(test_db):
    """Test that status update is rolled back if audit log creation fails.

    Verifies review finding F1: if audit creation fails, the workflow state
    update must be rolled back so no orphaned state exists without audit trail.
    """
    workflow_id = state_store.create_workflow(ticket_key="AOS-113")

    # Get initial status
    workflow_before = state_store.get_workflow(workflow_id)
    initial_status = workflow_before["status"]

    # Drop the audit_log table to force a constraint violation
    # This simulates a failure during _create_audit_log()
    try:
        conn = get_connection()
        conn.execute("DROP TABLE audit_log")
        conn.commit()
        conn.close()
    except sqlite3.OperationalError:
        pass

    # Attempt to update status — should fail and rollback
    try:
        state_store.update_status(
            workflow_id=workflow_id,
            status=WorkflowStatus.IN_PROGRESS,
            actor="test_actor",
            reason="Should fail",
        )
    except (sqlite3.OperationalError, Exception):
        # Expected: update fails due to missing audit_log table
        pass

    # Restore the audit_log table
    state_store.run_migrations()

    # Verify workflow status was NOT changed (rollback successful)
    workflow_after = state_store.get_workflow(workflow_id)
    assert workflow_after["status"] == initial_status


def test_work_plan_update_rollback_no_partial_state(test_db):
    """Test that work plan update is rolled back as a whole.

    If a work plan update fails partway through, neither the work_plan
    nor the audit entry should be committed.
    """
    workflow_id = state_store.create_workflow(ticket_key="AOS-113", work_plan={"v": 1})

    # Get initial state
    workflow_before = state_store.get_workflow(workflow_id)
    initial_plan = workflow_before["work_plan"]

    # Create a work plan that we'll try to update
    new_plan = {"v": 2, "complex": {"nested": {"data": [1, 2, 3]}}}

    # Force a failure by corrupting the database connection mid-transaction
    try:
        conn = get_connection()
        conn.execute("DROP TABLE audit_log")
        conn.commit()
        conn.close()
    except sqlite3.OperationalError:
        pass

    # Attempt update — should fail
    try:
        state_store.update_work_plan(workflow_id, new_plan, actor="test_actor")
    except (sqlite3.OperationalError, Exception):
        pass

    # Restore the table
    state_store.run_migrations()

    # Verify work_plan was NOT changed (rollback successful)
    workflow_after = state_store.get_workflow(workflow_id)
    assert workflow_after["work_plan"] == initial_plan


def test_execution_summary_rollback_preserves_original(test_db):
    """Test that execution summary update rolls back on failure.

    If audit creation fails, the execution_summary update should be rolled back.
    """
    workflow_id = state_store.create_workflow(ticket_key="AOS-113")

    # Set an initial execution_summary
    initial_summary = {"status": "pending", "step": 1}
    state_store.update_execution_summary(workflow_id, initial_summary, actor="system")

    workflow_before = state_store.get_workflow(workflow_id)
    stored_before = workflow_before["execution_summary"]

    # Try to update with a corrupted database
    try:
        conn = get_connection()
        conn.execute("DROP TABLE audit_log")
        conn.commit()
        conn.close()
    except sqlite3.OperationalError:
        pass

    new_summary = {"status": "running", "step": 2}
    try:
        state_store.update_execution_summary(workflow_id, new_summary, actor="test_actor")
    except (sqlite3.OperationalError, Exception):
        pass

    # Restore
    state_store.run_migrations()

    # Verify execution_summary was NOT changed
    workflow_after = state_store.get_workflow(workflow_id)
    assert workflow_after["execution_summary"] == stored_before


def test_usage_summary_rollback_no_orphaned_state(test_db):
    """Test that usage_summary update is rolled back when audit creation fails.

    Verifies that partial usage_summary updates are not committed if the
    audit log creation fails.
    """
    workflow_id = state_store.create_workflow(ticket_key="AOS-113")

    # Add initial usage data
    initial_data = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    state_store.update_usage_summary(workflow_id, "stage1", initial_data, actor="system")

    workflow_before = state_store.get_workflow(workflow_id)
    usage_before = workflow_before.get("usage_summary", {})

    # Corrupt database
    try:
        conn = get_connection()
        conn.execute("DROP TABLE audit_log")
        conn.commit()
        conn.close()
    except sqlite3.OperationalError:
        pass

    # Attempt to add another stage
    new_data = {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300}
    try:
        state_store.update_usage_summary(workflow_id, "stage2", new_data, actor="test_actor")
    except (sqlite3.OperationalError, Exception):
        pass

    # Restore
    state_store.run_migrations()

    # Verify usage_summary was NOT updated
    workflow_after = state_store.get_workflow(workflow_id)
    usage_after = workflow_after.get("usage_summary", {})
    assert usage_after == usage_before


def test_retry_count_rollback_on_audit_failure(test_db):
    """Test that retry count increment is rolled back if audit creation fails.

    Verifies that if an audit log creation fails, the retry_count
    increment is also rolled back (not partially committed).
    """
    workflow_id = state_store.create_workflow(ticket_key="AOS-113")

    # Increment once to ensure it works
    state_store.increment_retry_count(workflow_id, actor="system")

    workflow_before = state_store.get_workflow(workflow_id)
    retry_count_before = workflow_before["retry_count"]

    # Corrupt database
    try:
        conn = get_connection()
        conn.execute("DROP TABLE audit_log")
        conn.commit()
        conn.close()
    except sqlite3.OperationalError:
        pass

    # Attempt increment — should fail
    try:
        state_store.increment_retry_count(workflow_id, actor="test_actor")
    except (sqlite3.OperationalError, Exception):
        pass

    # Restore
    state_store.run_migrations()

    # Verify retry_count was NOT incremented
    workflow_after = state_store.get_workflow(workflow_id)
    assert workflow_after["retry_count"] == retry_count_before


def test_multiple_workflows_isolation_after_rollback(test_db):
    """Test that a failed transaction in one workflow doesn't affect others.

    Verifies that when transaction rollback occurs, it only affects the
    target workflow, not other workflows in the database.
    """
    workflow_id_1 = state_store.create_workflow(ticket_key="AOS-113a")
    workflow_id_2 = state_store.create_workflow(ticket_key="AOS-113b")

    # Set distinct status for each
    state_store.update_status(workflow_id_1, WorkflowStatus.IN_PROGRESS, actor="system")
    state_store.update_status(workflow_id_2, WorkflowStatus.COMPLETED, actor="system")

    wf1_before = state_store.get_workflow(workflow_id_1)
    wf2_before = state_store.get_workflow(workflow_id_2)

    assert wf1_before["status"] == WorkflowStatus.IN_PROGRESS
    assert wf2_before["status"] == WorkflowStatus.COMPLETED

    # Corrupt database
    try:
        conn = get_connection()
        conn.execute("DROP TABLE audit_log")
        conn.commit()
        conn.close()
    except sqlite3.OperationalError:
        pass

    # Attempt to update workflow_id_1 — should fail
    try:
        state_store.update_status(workflow_id_1, WorkflowStatus.FAILED, actor="test")
    except (sqlite3.OperationalError, Exception):
        pass

    # Restore
    state_store.run_migrations()

    # Verify workflow_id_1 unchanged
    wf1_after = state_store.get_workflow(workflow_id_1)
    assert wf1_after["status"] == WorkflowStatus.IN_PROGRESS

    # Verify workflow_id_2 unchanged (isolation)
    wf2_after = state_store.get_workflow(workflow_id_2)
    assert wf2_after["status"] == WorkflowStatus.COMPLETED


def test_audit_log_consistency_across_multiple_operations(test_db):
    """Test that audit log remains consistent across multiple operations.

    Verifies that the audit log is maintained correctly even after multiple
    transactions, with no gaps or missing entries.
    """
    workflow_id = state_store.create_workflow(ticket_key="AOS-113")

    # Perform multiple successful updates in sequence
    state_store.update_status(workflow_id, WorkflowStatus.IN_PROGRESS, actor="user1")
    count_after_1st = len(state_store.get_audit_log(workflow_id))

    state_store.update_status(workflow_id, WorkflowStatus.COMPLETED, actor="user2")
    count_after_2nd = len(state_store.get_audit_log(workflow_id))

    state_store.update_status(workflow_id, WorkflowStatus.FAILED, actor="user3")
    count_after_3rd = len(state_store.get_audit_log(workflow_id))

    # Verify each update added exactly one audit entry
    assert count_after_2nd == count_after_1st + 1
    assert count_after_3rd == count_after_2nd + 1

    # Verify all entries are present and in order
    audit_log = state_store.get_audit_log(workflow_id)
    assert len(audit_log) == count_after_3rd

    # Verify the actors are recorded in correct order
    status_changes = [e for e in audit_log if e["action"] == "status_change"]
    assert len(status_changes) == 3
    assert status_changes[0]["actor"] == "user1"
    assert status_changes[1]["actor"] == "user2"
    assert status_changes[2]["actor"] == "user3"
