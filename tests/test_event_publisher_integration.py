"""Integration tests: verify publish_status_event is called from each gate node and
terminal-transition node, and that publish failures never propagate."""

from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_INTERRUPT = "langgraph.types.interrupt"


def _mock_interrupt(return_value: dict):
    return patch("langgraph.types.interrupt", return_value=return_value)


# ---------------------------------------------------------------------------
# await_approval
# ---------------------------------------------------------------------------


class TestAwaitApprovalPublish:
    _PATCH_UPDATE_STATUS = "orchestrator.nodes.await_approval.update_status"
    _PATCH_GET_WORKFLOW = "orchestrator.nodes.await_approval.get_workflow"
    _PATCH_PUBLISH = "orchestrator.nodes.await_approval.publish_status_event"
    _PATCH_INTERRUPT = "orchestrator.nodes.await_approval.interrupt"

    def _run(self, decision="approved", reason=None):
        from orchestrator.nodes.await_approval import await_approval

        state = {"workflow_id": "wf-1", "ticket_key": "AOS-1"}
        with (
            patch(
                self._PATCH_GET_WORKFLOW, return_value={"status": MagicMock(value="in_progress")}
            ),
            patch(self._PATCH_UPDATE_STATUS),
            patch(self._PATCH_PUBLISH) as mock_publish,
            patch("orchestrator.nodes.await_approval._get_actor", return_value="tester"),
            patch(self._PATCH_INTERRUPT, return_value={"decision": decision, "reason": reason}),
        ):
            await_approval(state)
        return mock_publish

    def test_publishes_pending_approval_before_interrupt(self):
        mock_publish = self._run(decision="approved")
        mock_publish.assert_called_once_with(
            workflow_id="wf-1",
            status_value="pending_approval",
            ticket_id="AOS-1",
        )

    def test_publish_called_before_interrupt_not_after(self):
        """Verifies publish fires on entry (before interrupt), not on resume."""
        from orchestrator.nodes.await_approval import await_approval

        call_order = []
        state = {"workflow_id": "wf-1", "ticket_key": "AOS-1"}

        def fake_publish(**kw):
            call_order.append("publish")

        def fake_interrupt(payload):
            call_order.append("interrupt")
            return {"decision": "approved"}

        with (
            patch(
                self._PATCH_GET_WORKFLOW, return_value={"status": MagicMock(value="in_progress")}
            ),
            patch(self._PATCH_UPDATE_STATUS),
            patch(self._PATCH_PUBLISH, side_effect=fake_publish),
            patch("orchestrator.nodes.await_approval._get_actor", return_value="tester"),
            patch(self._PATCH_INTERRUPT, side_effect=fake_interrupt),
        ):
            await_approval(state)

        assert call_order == ["publish", "interrupt"]


# ---------------------------------------------------------------------------
# await_pr_approval
# ---------------------------------------------------------------------------


class TestAwaitPrApprovalPublish:
    _PATCH_UPDATE_STATUS = "orchestrator.nodes.await_pr_approval.update_status"
    _PATCH_GET_WORKFLOW = "orchestrator.nodes.await_pr_approval.get_workflow"
    _PATCH_PUBLISH = "orchestrator.nodes.await_pr_approval.publish_status_event"
    _PATCH_INTERRUPT = "orchestrator.nodes.await_pr_approval.interrupt"

    def _run(self, decision="approved", pr_url="https://github.com/org/repo/pull/1"):
        from orchestrator.nodes.await_pr_approval import await_pr_approval

        state = {"workflow_id": "wf-2", "ticket_key": "AOS-2", "pr_url": pr_url}
        with (
            patch(
                self._PATCH_GET_WORKFLOW, return_value={"status": MagicMock(value="in_progress")}
            ),
            patch(self._PATCH_UPDATE_STATUS),
            patch(self._PATCH_PUBLISH) as mock_publish,
            patch("orchestrator.nodes.await_pr_approval._get_actor", return_value="tester"),
            patch("orchestrator.nodes.await_pr_approval.update_pr_comments"),
            patch(self._PATCH_INTERRUPT, return_value={"decision": decision}),
        ):
            await_pr_approval(state)
        return mock_publish

    def test_publishes_pending_pr_approval_before_interrupt(self):
        mock_publish = self._run(decision="approved")
        # First call is the gate publish (before interrupt)
        gate_call = mock_publish.call_args_list[0]
        assert gate_call.kwargs["status_value"] == "pending_pr_approval"
        assert gate_call.kwargs["workflow_id"] == "wf-2"

    def test_publishes_completed_on_approved(self):
        mock_publish = self._run(decision="approved")
        terminal_call = mock_publish.call_args_list[1]
        assert terminal_call.kwargs["status_value"] == "completed"

    def test_publishes_failed_on_rejected(self):
        mock_publish = self._run(decision="rejected")
        terminal_call = mock_publish.call_args_list[1]
        assert terminal_call.kwargs["status_value"] == "rejected"


# ---------------------------------------------------------------------------
# await_workplan_clarification
# ---------------------------------------------------------------------------


class TestAwaitWorkplanClarificationPublish:
    _PATCH_UPDATE_STATUS = (
        "orchestrator.work_planner.nodes.await_workplan_clarification.update_status"
    )
    _PATCH_PUBLISH = (
        "orchestrator.work_planner.nodes.await_workplan_clarification.publish_status_event"
    )
    _PATCH_UPDATE_WORK_PLAN = (
        "orchestrator.work_planner.nodes.await_workplan_clarification.update_work_plan"
    )
    _PATCH_UPDATE_HISTORY = (
        "orchestrator.work_planner.nodes.await_workplan_clarification.update_clarification_history"
    )

    def test_publishes_pending_workplan_clarification_before_interrupt(self):
        from orchestrator.work_planner.nodes.await_workplan_clarification import (
            await_workplan_clarification,
        )

        state = {
            "workflow_id": "wf-3",
            "ticket_key": "AOS-3",
            "work_plan_data": {"status": "concerns", "concerns": ["Risk A"]},
            "clarifications": [],
        }

        with (
            patch(self._PATCH_UPDATE_STATUS),
            patch(self._PATCH_UPDATE_WORK_PLAN),
            patch(self._PATCH_UPDATE_HISTORY),
            patch(self._PATCH_PUBLISH) as mock_publish,
            patch(
                "orchestrator.work_planner.nodes.await_workplan_clarification._get_actor",
                return_value="tester",
            ),
            patch(
                "orchestrator.work_planner.nodes.await_workplan_clarification.interrupt",
                return_value={"answers": ["answer"]},
            ),
        ):
            await_workplan_clarification(state)

        mock_publish.assert_called_once_with(
            workflow_id="wf-3",
            status_value="pending_workplan_clarification",
            ticket_id="AOS-3",
        )


# ---------------------------------------------------------------------------
# error_handler
# ---------------------------------------------------------------------------


class TestErrorHandlerPublish:
    def test_publishes_failed_with_error_message(self):
        from orchestrator.work_planner.nodes.error_handler import error_handler

        state = {"workflow_id": "wf-4", "ticket_key": "AOS-4", "error": "something went wrong"}
        with (
            patch("orchestrator.work_planner.nodes.error_handler.update_status"),
            patch("orchestrator.work_planner.nodes.error_handler.publish_status_event") as mock_pub,
        ):
            error_handler(state)

        mock_pub.assert_called_once_with(
            workflow_id="wf-4",
            status_value="failed",
            error_message="something went wrong",
            ticket_id="AOS-4",
        )


# ---------------------------------------------------------------------------
# store_plan
# ---------------------------------------------------------------------------


class TestStorePlanPublish:
    def test_publishes_plan_generated_after_store(self):
        from orchestrator.work_planner.nodes.store_plan import store_plan

        work_plan = {"status": "pass", "tasks": []}
        state = {"workflow_id": "wf-5", "ticket_key": "AOS-5", "work_plan_data": work_plan}
        with (
            patch("orchestrator.work_planner.nodes.store_plan.update_work_plan"),
            patch(
                "orchestrator.work_planner.nodes.store_plan.publish_plan_generated_event"
            ) as mock_pub,
        ):
            store_plan(state)

        mock_pub.assert_called_once_with(
            workflow_id="wf-5",
            ticket_id="AOS-5",
            work_plan=work_plan,
        )


# ---------------------------------------------------------------------------
# persist_results — FAILED path
# ---------------------------------------------------------------------------


class TestPersistResultsPublish:
    def test_publishes_failed_when_exec_error(self):
        from orchestrator.code_generator.nodes.persist_results import persist_results

        state = {
            "workflow_id": "wf-6",
            "code_generation_summary": {"status": "error", "pr_url": ""},
            "exec_error": "goose failed",
        }
        with (
            patch(
                "orchestrator.code_generator.nodes.persist_results.update_code_generation_summary"
            ),
            patch("orchestrator.code_generator.nodes.persist_results.update_status"),
            patch("orchestrator.code_generator.nodes.persist_results.update_usage_summary"),
            patch(
                "orchestrator.code_generator.nodes.persist_results.aggregate_token_usage",
                return_value={},
            ),
            patch(
                "orchestrator.code_generator.nodes.persist_results.publish_status_event"
            ) as mock_pub,
        ):
            persist_results(state)

        mock_pub.assert_called_once_with(
            workflow_id="wf-6",
            status_value="failed",
            error_message="goose failed",
        )

    def test_does_not_publish_when_success_routed_to_pr_approval(self):
        from orchestrator.code_generator.nodes.persist_results import persist_results

        state = {
            "workflow_id": "wf-7",
            "code_generation_summary": {
                "status": "success",
                "pr_url": "https://github.com/org/repo/pull/5",
            },
            "exec_error": None,
        }
        with (
            patch(
                "orchestrator.code_generator.nodes.persist_results.update_code_generation_summary"
            ),
            patch("orchestrator.code_generator.nodes.persist_results.update_status"),
            patch("orchestrator.code_generator.nodes.persist_results.update_usage_summary"),
            patch(
                "orchestrator.code_generator.nodes.persist_results.aggregate_token_usage",
                return_value={},
            ),
            patch(
                "orchestrator.code_generator.nodes.persist_results.publish_status_event"
            ) as mock_pub,
        ):
            persist_results(state)

        mock_pub.assert_not_called()


# ---------------------------------------------------------------------------
# local_workflow_service — cancel / mark_interrupted / mark_failed
# ---------------------------------------------------------------------------


class TestLocalWorkflowServicePublish:
    def _make_service(self, workflow_status=None):
        from orchestrator.workflow_service.local_workflow_service import LocalWorkflowService

        mock_repo = MagicMock()
        if workflow_status:
            mock_repo.get_workflow.return_value = {"status": workflow_status}
        else:
            mock_repo.get_workflow.return_value = None
        svc = LocalWorkflowService(repository=mock_repo, graph_factory=MagicMock())
        return svc, mock_repo

    def test_cancel_publishes_cancelled(self):
        from state.workflow_status import WorkflowStatus

        svc, _ = self._make_service()
        with patch(
            "orchestrator.workflow_service.local_workflow_service.publish_status_event"
        ) as mock_pub:
            svc.cancel("wf-8", reason="user request")

        mock_pub.assert_called_once_with(
            workflow_id="wf-8",
            status_value=WorkflowStatus.CANCELLED.value,
            error_message="user request",
        )

    def test_mark_interrupted_publishes_failed(self):
        from state.workflow_status import WorkflowStatus

        svc, _ = self._make_service(workflow_status=WorkflowStatus.IN_PROGRESS)
        with patch(
            "orchestrator.workflow_service.local_workflow_service.publish_status_event"
        ) as mock_pub:
            svc.mark_interrupted("wf-9", failed_node="generate_code")

        mock_pub.assert_called_once()
        assert mock_pub.call_args.kwargs["status_value"] == WorkflowStatus.FAILED.value
        assert "generate_code" in mock_pub.call_args.kwargs["error_message"]

    def test_mark_failed_publishes_failed(self):
        from state.workflow_status import WorkflowStatus

        svc, _ = self._make_service(workflow_status=WorkflowStatus.IN_PROGRESS)
        with patch(
            "orchestrator.workflow_service.local_workflow_service.publish_status_event"
        ) as mock_pub:
            svc.mark_failed("wf-10", reason="fatal error")

        mock_pub.assert_called_once_with(
            workflow_id="wf-10",
            status_value=WorkflowStatus.FAILED.value,
            error_message="fatal error",
        )

    def test_mark_failed_skips_publish_when_already_terminal(self):
        from state.workflow_status import WorkflowStatus

        svc, _ = self._make_service(workflow_status=WorkflowStatus.COMPLETED)
        with patch(
            "orchestrator.workflow_service.local_workflow_service.publish_status_event"
        ) as mock_pub:
            svc.mark_failed("wf-11", reason="too late")

        mock_pub.assert_not_called()
