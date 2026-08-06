"""Unit tests for orchestrator/event_publisher/mapper.py."""

import pytest

from orchestrator.event_publisher.mapper import map_status, plan_generated_event_type
from state.workflow_status import WorkflowStatus


class TestMapStatus:
    """Table-driven: every mapped WorkflowStatus must produce the correct wire pair."""

    @pytest.mark.parametrize(
        "status, expected_event_type, expected_status",
        [
            (WorkflowStatus.IN_PROGRESS, "execution.started", "RUNNING"),
            (WorkflowStatus.PENDING_APPROVAL, "approval.pending", "PENDING_APPROVAL"),
            (WorkflowStatus.PENDING_PR_APPROVAL, "pr_approval.pending", "PENDING_PR_APPROVAL"),
            (
                WorkflowStatus.PENDING_WORKPLAN_CLARIFICATION,
                "workplan_clarification.pending",
                "PENDING_WORKPLAN_CLARIFICATION",
            ),
            (WorkflowStatus.COMPLETED, "execution.completed", "SUCCEEDED"),
            (WorkflowStatus.FAILED, "execution.failed", "FAILED"),
            (WorkflowStatus.CANCELLED, "execution.failed", "CANCELLED"),
            (WorkflowStatus.REJECTED, "execution.failed", "FAILED"),
        ],
    )
    def test_known_status(self, status, expected_event_type, expected_status):
        event_type, wire_status = map_status(status.value)
        assert event_type == expected_event_type
        assert wire_status == expected_status

    @pytest.mark.parametrize(
        "status",
        [
            WorkflowStatus.PENDING,
            WorkflowStatus.APPROVED,
            WorkflowStatus.PR_COMMENTED,
        ],
    )
    def test_unmapped_status_raises(self, status):
        with pytest.raises(ValueError, match="No event mapping"):
            map_status(status.value)

    def test_unknown_string_raises(self):
        with pytest.raises(ValueError, match="No event mapping"):
            map_status("completely_unknown")


class TestPlanGeneratedEventType:
    def test_returns_plan_generated(self):
        assert plan_generated_event_type() == "plan.generated"
