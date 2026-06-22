"""Tests for the dispatcher's fire-and-forget SSE follower (AOS-149).

The follower owns the bridge between the dispatcher's ``202 Accepted`` HTTP
submissions and the operator's CLI feedback loop: in remote mode every
``submit_and_follow`` call must subscribe to the lifecycle event stream and
re-read the workflow detail once the stream terminates, so the existing
handler post-processing (status banners, JIRA comments) keeps seeing a
``WorkflowRunResult`` whose ``final_status`` matches the server's view.

These tests use a tiny fake service that mimics just enough of the HTTP
contract; they intentionally do not spin up a real FastAPI app — that path
is exercised by :mod:`tests.test_dispatcher_remote`.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional, cast

import pytest
from click.testing import CliRunner

from dispatcher.commands.follow import (
    follow_workflow,
    submit_and_follow,
)
from dispatcher.run import run
from orchestrator.workflow_service import WorkflowService
from orchestrator.workflow_service.dtos import (
    WorkflowDetail,
    WorkflowEvent,
    WorkflowRunResult,
)
from orchestrator.workflow_service.local import LocalWorkflowService
from state.workflow_status import WorkflowStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detail(
    workflow_id: str,
    *,
    status: WorkflowStatus,
    ticket_key: str = "AOS-149",
    pr_url: Optional[str] = None,
    execution_summary: Optional[dict[str, Any]] = None,
) -> WorkflowDetail:
    return WorkflowDetail(
        id=workflow_id,
        ticket_key=ticket_key,
        status=status,
        created_at="2026-06-22T00:00:00",
        updated_at="2026-06-22T00:00:00",
        pr_url=pr_url,
        work_plan=None,
        execution_summary=execution_summary,
        clarification_history=[],
        pr_comments=None,
        usage_summary={},
        retry_count=0,
    )


def _event(workflow_id: str, seq: int, kind: str, *, node: Optional[str] = None) -> WorkflowEvent:
    return WorkflowEvent(workflow_id=workflow_id, seq=seq, kind=kind, node=node, data=None)


class _FakeRemoteService:
    """Mimics the surface ``submit_and_follow`` needs from an HTTP service.

    Importantly we register this class as a virtual subclass of
    :class:`HttpWorkflowService` so the production ``is_remote_service``
    check returns True without us having to instantiate a real httpx client.
    """

    def __init__(self) -> None:
        self.events_to_emit: List[WorkflowEvent] = []
        self.stream_calls: List[tuple[str, int]] = []
        self.workflows: dict[str, WorkflowDetail] = {}

    def stream_events(self, workflow_id: str, after_seq: int = 0) -> Iterable[WorkflowEvent]:
        self.stream_calls.append((workflow_id, after_seq))
        return iter(self.events_to_emit)

    def get(self, workflow_id: str) -> Optional[WorkflowDetail]:
        return self.workflows.get(workflow_id)


@pytest.fixture(autouse=True)
def _register_fake_as_remote(monkeypatch):
    """Make ``isinstance(fake, HttpWorkflowService)`` return True for the tests.

    We patch ``is_remote_service`` rather than mutating the real class so
    the production code path stays unmonkeyed-with for every other test in
    the suite.
    """
    from dispatcher.commands import follow as follow_mod

    monkeypatch.setattr(
        follow_mod,
        "is_remote_service",
        lambda svc: isinstance(svc, _FakeRemoteService),
    )


# ---------------------------------------------------------------------------
# is_remote_service
# ---------------------------------------------------------------------------


def test_is_remote_service_rejects_local_service(tmp_path) -> None:
    # Build the local service via the constructor that doesn't touch the
    # network.  Reach past the monkeypatch by calling the *real* helper.
    from dispatcher.commands.follow import is_remote_service as real_check

    svc = cast(WorkflowService, object.__new__(LocalWorkflowService))
    # The check only looks at the type, not at any constructed state.
    assert real_check(svc) is False


# ---------------------------------------------------------------------------
# follow_workflow
# ---------------------------------------------------------------------------


class TestFollowWorkflow:
    def test_local_service_is_a_noop(self) -> None:
        """The local transport runs synchronously, so the follower must
        return ``None`` without trying to subscribe to anything."""
        result = follow_workflow(cast(WorkflowService, object()), "wf-x")
        assert result is None

    def test_stops_on_terminal_completed_event(self, capsys) -> None:
        svc = _FakeRemoteService()
        svc.events_to_emit = [
            _event("wf-1", 1, "node_start", node="plan"),
            _event("wf-1", 2, "node_end", node="plan"),
            _event("wf-1", 3, "completed"),
            # Anything after the terminal event must NOT be consumed.
            _event("wf-1", 4, "node_start", node="ghost"),
        ]

        terminal = follow_workflow(cast(WorkflowService, svc), "wf-1")

        assert terminal is not None
        assert terminal.kind == "completed"
        assert svc.stream_calls == [("wf-1", 0)]
        out = capsys.readouterr().out
        assert "Following workflow wf-1" in out
        assert "ghost" not in out  # didn't drain past the terminal event

    def test_stops_on_interrupt_for_human_gates(self, capsys) -> None:
        svc = _FakeRemoteService()
        svc.events_to_emit = [
            _event("wf-2", 1, "node_start", node="generate"),
            _event("wf-2", 2, "interrupt", node="await_approval"),
        ]

        terminal = follow_workflow(cast(WorkflowService, svc), "wf-2")

        assert terminal is not None
        assert terminal.kind == "interrupt"

    def test_keyboard_interrupt_detaches_without_raising(self, capsys) -> None:
        """Ctrl-C during streaming must leave the workflow alone and just exit."""

        class _RudeService(_FakeRemoteService):
            def stream_events(self, workflow_id, after_seq=0):
                yield _event(workflow_id, 1, "node_start", node="plan")
                raise KeyboardInterrupt

        svc = _RudeService()
        terminal = follow_workflow(cast(WorkflowService, svc), "wf-3")
        # Last seen event is returned even on Ctrl-C — handy for callers.
        assert terminal is not None
        assert terminal.kind == "node_start"
        err = capsys.readouterr().err
        assert "Detached" in err


# ---------------------------------------------------------------------------
# submit_and_follow
# ---------------------------------------------------------------------------


class TestSubmitAndFollow:
    def test_local_mode_returns_op_result_unchanged(self) -> None:
        """For local services we must NOT touch the SSE stream or re-read
        the detail — the op already ran synchronously and the returned
        result is authoritative."""
        local = cast(WorkflowService, object())  # is_remote_service patched to False

        sentinel = WorkflowRunResult(
            workflow_id="wf-local",
            ticket_key="AOS-149",
            final_status=WorkflowStatus.COMPLETED,
            interrupted=False,
        )

        calls: List[tuple] = []

        def op(*args: Any, **kwargs: Any) -> WorkflowRunResult:
            calls.append((args, kwargs))
            return sentinel

        result = submit_and_follow(local, op, "arg1", workflow_id_hint="wf-local")
        assert result is sentinel
        assert calls == [(("arg1",), {})]

    def test_detach_skips_follower_and_returns_initial_snapshot(self) -> None:
        svc = _FakeRemoteService()
        # If the follower were invoked the test would hang on this empty
        # generator — but it must NOT be invoked.
        svc.events_to_emit = []

        initial = WorkflowRunResult(
            workflow_id="wf-7",
            ticket_key="AOS-149",
            final_status=WorkflowStatus.PENDING,
            interrupted=False,
        )

        result = submit_and_follow(cast(WorkflowService, svc), lambda: initial, detach=True)
        assert result is initial
        assert svc.stream_calls == []

    def test_remote_mode_follows_then_refreshes_detail(self) -> None:
        svc = _FakeRemoteService()
        svc.events_to_emit = [_event("wf-9", 1, "completed")]
        svc.workflows["wf-9"] = _detail(
            "wf-9",
            status=WorkflowStatus.COMPLETED,
            pr_url="https://github.com/example/repo/pull/42",
            execution_summary={"status": "all green"},
        )

        # 202 snapshot from the server: just enough to identify the workflow,
        # without the real ``final_status`` (that arrives via the follower).
        initial = WorkflowRunResult(
            workflow_id="wf-9",
            ticket_key="AOS-149",
            final_status=WorkflowStatus.PENDING,
            interrupted=False,
        )

        result = submit_and_follow(cast(WorkflowService, svc), lambda: initial)

        assert result.workflow_id == "wf-9"
        # The result is now refreshed from server state, not the 202 snapshot.
        assert result.final_status == WorkflowStatus.COMPLETED
        assert result.pr_url == "https://github.com/example/repo/pull/42"
        assert result.execution_summary == {"status": "all green"}
        # We subscribed to the lifecycle stream exactly once, from seq 0.
        assert svc.stream_calls == [("wf-9", 0)]

    def test_remote_mode_marks_interrupted_when_paused_at_human_gate(self) -> None:
        svc = _FakeRemoteService()
        svc.events_to_emit = [_event("wf-10", 1, "interrupt", node="await_approval")]
        svc.workflows["wf-10"] = _detail("wf-10", status=WorkflowStatus.PENDING_APPROVAL)

        initial = WorkflowRunResult(
            workflow_id="wf-10",
            ticket_key="AOS-149",
            final_status=WorkflowStatus.PENDING,
            interrupted=False,
        )

        result = submit_and_follow(cast(WorkflowService, svc), lambda: initial)

        assert result.final_status == WorkflowStatus.PENDING_APPROVAL
        # Paused workflows are flagged so the handler shows the "awaiting input"
        # banner instead of "completed".
        assert result.interrupted is True

    def test_remote_mode_falls_back_to_initial_when_detail_disappears(self) -> None:
        """If a concurrent process purges the workflow row between the follow
        loop and the refresh, the helper must NOT crash — it falls back to
        the 202 snapshot."""
        svc = _FakeRemoteService()
        svc.events_to_emit = [_event("wf-11", 1, "completed")]
        # NOTE: no entry in svc.workflows for wf-11.

        initial = WorkflowRunResult(
            workflow_id="wf-11",
            ticket_key="AOS-149",
            final_status=WorkflowStatus.PENDING,
            interrupted=False,
        )

        result = submit_and_follow(cast(WorkflowService, svc), lambda: initial)
        assert result is initial

    def test_workflow_id_hint_used_when_initial_lacks_id(self) -> None:
        """Some 202 snapshots may omit the workflow id (server hiccup, fake
        responses); the caller-supplied hint is the authoritative fallback."""
        svc = _FakeRemoteService()
        svc.events_to_emit = [_event("wf-hint", 1, "completed")]
        svc.workflows["wf-hint"] = _detail("wf-hint", status=WorkflowStatus.COMPLETED)

        initial = WorkflowRunResult(
            workflow_id="",
            ticket_key="AOS-149",
            final_status=WorkflowStatus.PENDING,
            interrupted=False,
        )

        result = submit_and_follow(
            cast(WorkflowService, svc), lambda: initial, workflow_id_hint="wf-hint"
        )
        assert result.workflow_id == "wf-hint"
        assert result.final_status == WorkflowStatus.COMPLETED


# ---------------------------------------------------------------------------
# CLI: --detach guardrail
# ---------------------------------------------------------------------------


class TestDetachFlagGuardrail:
    """``--detach`` is only meaningful for HTTP fire-and-forget submissions.

    Passing it against the in-process local service almost certainly means
    the operator misunderstood the mode they are running in (or forgot to
    set ``ORCHESTRATOR_MODE=remote``), so the CLI must fail loudly.
    """

    def test_detach_with_local_service_exits_nonzero(self, monkeypatch) -> None:
        from dispatcher.commands import follow as follow_mod

        # Re-enable the real is_remote_service check for this CLI test
        # (the autouse fixture rewires it to recognise the in-memory fake).
        monkeypatch.setattr(
            follow_mod,
            "is_remote_service",
            lambda svc: False,
        )

        local_service = object()  # CLI won't dereference it before --detach check
        runner = CliRunner()
        result = runner.invoke(run, ["--detach", "--ticket", "AOS-149"], obj=local_service)

        assert result.exit_code == 2, result.output
        assert "--detach" in result.output
        assert "remote" in result.output
