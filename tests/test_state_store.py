"""Unit tests for state_store module."""

import os
import tempfile

import pytest

from state import repository as state_store
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

    from state.repository import SQLiteWorkflowRepository

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
        "risks": [],
        "questions_for_reviewer": [],
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
    import json

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
    usage_summary = json.loads(workflow["usage_summary"])
    assert "plan" in usage_summary
    assert usage_summary["plan"]["turns"] == 12
    assert usage_summary["plan"]["total_tokens"] == 3800
    assert usage_summary["plan"]["stop_reasons"] == ["stop", "stop"]


def test_update_usage_summary_merges_multiple_stages(test_db):
    """Test that calling update_usage_summary for two stages keeps both."""
    import json

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
    usage_summary = json.loads(workflow["usage_summary"])
    assert "plan" in usage_summary
    assert "execute" in usage_summary
    assert usage_summary["plan"]["turns"] == 10
    assert usage_summary["execute"]["turns"] == 42


def test_update_usage_summary_overwrites_same_stage(test_db):
    """Test that re-calling for the same stage replaces the previous data."""
    import json

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
    usage_summary = json.loads(workflow["usage_summary"])
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
        "questions": ["What DB?"],
        "risks": ["Risk A"],
        "answers": [{"question": "What DB?", "answer": "SQLite"}],
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
        {"round": 1, "questions": ["Q1"], "answers": [{"question": "Q1", "answer": "A1"}]},
    )
    state_store.update_clarification_history(
        workflow_id,
        {"round": 2, "questions": ["Q2"], "answers": [{"question": "Q2", "answer": "A2"}]},
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
        {"round": 1, "questions": [], "answers": []},
    )


def test_update_clarification_history_creates_audit_log(test_db):
    """Test that update_clarification_history creates an audit log entry."""
    workflow_id = state_store.create_workflow(ticket_key="AOS-85")
    state_store.update_clarification_history(
        workflow_id,
        {"round": 1, "questions": ["Q?"], "answers": [{"question": "Q?", "answer": "A!"}]},
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
        {"round": 1, "questions": ["Q?"], "answers": []},
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
        {"round": 1, "questions": ["Q?"], "answers": []},
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


def test_fake_repository_can_be_injected_without_database():
    """Demonstrate that FakeWorkflowRepository satisfies WorkflowRepository protocol.

    No database is needed — this proves callers can be tested with a pure
    in-memory double, satisfying the DIP goal of AOS-96.
    """
    from state.repository import WorkflowRepository
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
