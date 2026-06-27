"""Unit tests for ``dispatcher.tui.action_registry``.

These tests exercise the predicate for every action against the full
``WorkflowStatus`` matrix plus the no-selection case. They guard against
silent regressions where a graph route change adds a new lifecycle status
and an action is accidentally enabled (or hidden) for it.
"""

from __future__ import annotations

from typing import Dict, Optional

import pytest

from dispatcher.tui.action_registry import REGISTRY, WorkflowAction, action_for
from orchestrator.workflow_service import WorkflowDetail
from state.workflow_status import WorkflowStatus


def _detail(
    status: WorkflowStatus,
    *,
    pr_url: Optional[str] = None,
    work_plan: Optional[Dict[str, object]] = None,
) -> WorkflowDetail:
    return WorkflowDetail(
        id="wf-1",
        ticket_key="AOS-1",
        status=status,
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T01:00:00+00:00",
        pr_url=pr_url,
        work_plan=work_plan,
    )


def _entry(action: str) -> WorkflowAction:
    found = action_for(action)
    assert found is not None, f"action {action!r} not in REGISTRY"
    return found


class TestNoSelection:
    """When no row is selected, only globally-available actions show."""

    @pytest.mark.parametrize("action", ["refresh", "new_run", "clear_db"])
    def test_global_actions_visible(self, action: str) -> None:
        assert _entry(action).applies(None) is True

    @pytest.mark.parametrize(
        "action",
        ["approve", "reject", "clarify", "retry", "cancel", "approve_pr", "comment_pr", "logs"],
    )
    def test_workflow_scoped_actions_hidden(self, action: str) -> None:
        assert _entry(action).applies(None) is False


class TestApprovePredicate:
    def test_visible_only_in_pending_approval(self) -> None:
        for status in WorkflowStatus:
            applies = _entry("approve").applies(_detail(status))
            assert applies is (status == WorkflowStatus.PENDING_APPROVAL), status

    def test_reject_mirrors_approve(self) -> None:
        for status in WorkflowStatus:
            applies = _entry("reject").applies(_detail(status))
            assert applies is (status == WorkflowStatus.PENDING_APPROVAL), status


class TestClarifyPredicate:
    def test_hidden_when_no_concerns(self) -> None:
        detail = _detail(WorkflowStatus.PENDING_WORKPLAN_CLARIFICATION, work_plan={"concerns": []})
        assert _entry("clarify").applies(detail) is False

    def test_hidden_when_work_plan_missing(self) -> None:
        detail = _detail(WorkflowStatus.PENDING_WORKPLAN_CLARIFICATION, work_plan=None)
        assert _entry("clarify").applies(detail) is False

    def test_visible_when_concerns_present(self) -> None:
        detail = _detail(
            WorkflowStatus.PENDING_WORKPLAN_CLARIFICATION,
            work_plan={"concerns": ["What about retries?"]},
        )
        assert _entry("clarify").applies(detail) is True

    def test_hidden_for_other_statuses_even_with_concerns(self) -> None:
        for status in WorkflowStatus:
            if status == WorkflowStatus.PENDING_WORKPLAN_CLARIFICATION:
                continue
            detail = _detail(status, work_plan={"concerns": ["x"]})
            assert _entry("clarify").applies(detail) is False, status


class TestRetryPredicate:
    def test_matches_is_retryable(self) -> None:
        for status in WorkflowStatus:
            assert _entry("retry").applies(_detail(status)) is status.is_retryable(), status


class TestCancelPredicate:
    def test_matches_is_active(self) -> None:
        for status in WorkflowStatus:
            assert _entry("cancel").applies(_detail(status)) is status.is_active(), status


class TestPRPredicates:
    @pytest.mark.parametrize("action", ["approve_pr", "comment_pr"])
    def test_hidden_without_pr_url(self, action: str) -> None:
        detail = _detail(WorkflowStatus.PENDING_PR_APPROVAL, pr_url=None)
        assert _entry(action).applies(detail) is False

    @pytest.mark.parametrize("action", ["approve_pr", "comment_pr"])
    def test_visible_when_pending_pr_with_url(self, action: str) -> None:
        detail = _detail(
            WorkflowStatus.PENDING_PR_APPROVAL,
            pr_url="https://github.com/org/repo/pull/1",
        )
        assert _entry(action).applies(detail) is True

    @pytest.mark.parametrize("action", ["approve_pr", "comment_pr"])
    def test_hidden_for_other_statuses_even_with_pr(self, action: str) -> None:
        for status in WorkflowStatus:
            if status == WorkflowStatus.PENDING_PR_APPROVAL:
                continue
            detail = _detail(status, pr_url="https://github.com/org/repo/pull/1")
            assert _entry(action).applies(detail) is False, status


class TestLogsPredicate:
    def test_visible_for_any_selected_workflow(self) -> None:
        for status in WorkflowStatus:
            assert _entry("logs").applies(_detail(status)) is True


class TestRegistryShape:
    def test_action_for_unknown_returns_none(self) -> None:
        assert action_for("does-not-exist") is None

    def test_no_duplicate_keys(self) -> None:
        keys = [a.key for a in REGISTRY]
        assert len(keys) == len(set(keys)), f"duplicate keys: {keys}"

    def test_no_duplicate_actions(self) -> None:
        names = [a.action for a in REGISTRY]
        assert len(names) == len(set(names)), f"duplicate actions: {names}"

    def test_no_collision_with_global_bindings(self) -> None:
        """``quit`` (q) and ``toggle_tail_pause`` (space) are reserved for
        ``WorkflowTUI.BINDINGS`` outside the registry — ensure no entry
        steals those keys or action names."""
        reserved_keys = {"q", "space"}
        reserved_actions = {"quit", "toggle_tail_pause"}
        for entry in REGISTRY:
            assert entry.key not in reserved_keys, entry
            assert entry.action not in reserved_actions, entry
