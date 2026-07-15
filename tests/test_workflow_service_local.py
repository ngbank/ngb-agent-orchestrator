"""Unit tests for :mod:`orchestrator.workflow_service.local_workflow_service`.

These tests cover every public method of ``LocalWorkflowService`` using:
  * The real SQLite ``WorkflowRepository`` against a per-test temp DB
    (mirroring :mod:`tests.test_state_store`).
  * A ``FakeGraph`` that records calls and serves canned state /
    state_history without booting LangGraph.

No dispatcher CLI or TUI code is exercised — those layers are wired in by
separate integration test suites.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from langgraph.errors import GraphInterrupt
from langgraph.types import Command

from orchestrator.paths import workflow_logs_dir
from orchestrator.workflow_service import (
    LocalWorkflowService,
    WorkflowEvent,
    WorkflowStartRequest,
    WorkflowSummary,
)
from state import workflow_repository as state_store
from state.sqlite_workflow_repository import SQLiteWorkflowRepository
from state.workflow_status import WorkflowStatus

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_db(monkeypatch):
    """Create a fresh SQLite DB per test.

    The conftest autouse fixtures (``_isolate_xdg_state_home`` +
    ``_isolate_db_path``) already redirect XDG state, ``LOGS_DIR``, and
    ``DB_PATH`` to a pytest tmp tree; this fixture overrides ``DB_PATH``
    once more to satisfy tests that share a DB across a class scope.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        monkeypatch.setenv("DB_PATH", db_path)
        state_store.run_migrations()
        yield db_path


@pytest.fixture
def repo():
    return SQLiteWorkflowRepository()


# ---------------------------------------------------------------------------
# FakeGraph — minimal stand-in for the langgraph CompiledGraph
# ---------------------------------------------------------------------------


class _FakeTask:
    def __init__(
        self,
        name: str,
        *,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        interrupts: tuple = (),
    ) -> None:
        self.name = name
        self.result = result
        self.error = error
        self.interrupts = interrupts


class _FakeStateSnapshot:
    def __init__(
        self,
        *,
        values: Optional[Dict[str, Any]] = None,
        next_nodes: tuple = (),
        tasks: Optional[List[_FakeTask]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.values = dict(values or {})
        self.next = next_nodes
        self.tasks = tasks or []
        self.metadata = metadata or {}
        self.config = config or {}


class FakeGraph:
    """Test double for the LangGraph CompiledGraph.

    Records every call so tests can assert on the inputs the service passes,
    and serves canned ``get_state`` / ``get_state_history`` responses.
    Set ``stream_raises`` to simulate ``GraphInterrupt``.
    """

    def __init__(
        self,
        *,
        stream_events: Optional[List[Any]] = None,
        stream_raises: Optional[BaseException] = None,
        history: Optional[List[_FakeStateSnapshot]] = None,
        state: Optional[_FakeStateSnapshot] = None,
        post_stream_state: Optional[_FakeStateSnapshot] = None,
    ) -> None:
        self._stream_events = stream_events or []
        self._stream_raises = stream_raises
        # ``history`` is newest-first to match langgraph's contract; the
        # service reverses internally.
        self._history = history or []
        self._initial_state = state or _FakeStateSnapshot()
        self._post_stream_state = post_stream_state or self._initial_state
        self._stream_called = False
        self.stream_calls: List[tuple] = []
        self.update_state_calls: List[tuple] = []

    def stream(self, *args, **kwargs):
        self.stream_calls.append((args, kwargs))
        self._stream_called = True
        if self._stream_raises is not None:
            raise self._stream_raises
        return iter(self._stream_events)

    def get_state(self, config):
        # Once the stream has been consumed, return the post-stream state so
        # tests can assert the service reads the *final* state.
        return self._post_stream_state if self._stream_called else self._initial_state

    def get_state_history(self, config):
        return iter(self._history)

    def update_state(self, config, values):
        self.update_state_calls.append((config, values))
        # Propagate the update into the post-stream state so subsequent
        # get_state calls see the change.
        self._post_stream_state.values.update(values)


def _make_service(repo, graph: FakeGraph) -> LocalWorkflowService:
    return LocalWorkflowService(repository=repo, graph_factory=lambda: graph)


# ---------------------------------------------------------------------------
# Read-side wrappers
# ---------------------------------------------------------------------------


class TestReads:
    def test_get_returns_none_when_missing(self, temp_db, repo):
        svc = _make_service(repo, FakeGraph())
        assert svc.get("does-not-exist") is None

    def test_get_returns_workflow_detail(self, temp_db, repo):
        wf_id = repo.create_workflow(
            ticket_key="AOS-1",
            work_plan={"summary": "demo"},
            status=WorkflowStatus.PENDING,
        )
        svc = _make_service(repo, FakeGraph())
        detail = svc.get(wf_id)
        assert detail is not None
        assert detail.id == wf_id
        assert detail.ticket_key == "AOS-1"
        assert detail.status == WorkflowStatus.PENDING
        assert detail.work_plan == {"summary": "demo"}

    def test_get_by_ticket_returns_summaries_newest_first(self, temp_db, repo):
        wf1 = repo.create_workflow(ticket_key="AOS-2")
        wf2 = repo.create_workflow(ticket_key="AOS-2")
        svc = _make_service(repo, FakeGraph())
        results = svc.get_by_ticket("AOS-2")
        assert [r.id for r in results] == [wf2, wf1]
        for r in results:
            assert isinstance(r, WorkflowSummary)

    def test_get_latest_retryable_by_ticket(self, temp_db, repo):
        wf_id = repo.create_workflow(ticket_key="AOS-3")
        repo.update_status(wf_id, WorkflowStatus.FAILED)
        svc = _make_service(repo, FakeGraph())
        result = svc.get_latest_retryable_by_ticket("AOS-3")
        assert result is not None
        assert result.id == wf_id
        assert svc.get_latest_retryable_by_ticket("AOS-nope") is None

    def test_list_respects_status_filter(self, temp_db, repo):
        wf_done = repo.create_workflow(ticket_key="AOS-4")
        repo.update_status(wf_done, WorkflowStatus.COMPLETED)
        repo.create_workflow(ticket_key="AOS-4", status=WorkflowStatus.PENDING)

        svc = _make_service(repo, FakeGraph())
        completed = svc.list(status=WorkflowStatus.COMPLETED)
        assert {w.id for w in completed} == {wf_done}

    def test_get_audit_log(self, temp_db, repo):
        wf_id = repo.create_workflow(ticket_key="AOS-5")
        repo.update_status(wf_id, WorkflowStatus.IN_PROGRESS, actor="alice")

        svc = _make_service(repo, FakeGraph())
        entries = svc.get_audit_log(wf_id)
        assert any(e.action == "status_change" and e.actor == "alice" for e in entries)


# ---------------------------------------------------------------------------
# Log file reads
# ---------------------------------------------------------------------------


class TestReadLogs:
    def test_returns_empty_when_no_files(self, temp_db, repo):
        wf_id = repo.create_workflow(ticket_key="AOS-6")
        svc = _make_service(repo, FakeGraph())
        assert svc.read_logs(wf_id) == []

    def test_returns_chunks_for_existing_workflow_log(self, temp_db, repo):
        wf_id = repo.create_workflow(ticket_key="AOS-7")
        workflow_log = workflow_logs_dir(wf_id) / "workflow.log"
        workflow_log.write_text("workflow output here", encoding="utf-8")

        svc = _make_service(repo, FakeGraph())
        chunks = svc.read_logs(wf_id)
        assert len(chunks) == 1
        assert chunks[0].stage == "workflow"
        assert chunks[0].content == "workflow output here"
        assert Path(chunks[0].path) == workflow_log

    def test_incremental_read_does_not_reread_entire_file(self, temp_db, repo, monkeypatch):
        """``read_logs`` with ``after_offset > 0`` must seek instead of
        reading the whole file into memory and slicing.

        Regression guard: the previous implementation did
        ``path.read_bytes()`` on every poll, which combined with the SSE
        handler's sync calls could starve the FastAPI event loop on slow
        filesystems.
        """
        wf_id = repo.create_workflow(ticket_key="AOS-8")
        workflow_log = workflow_logs_dir(wf_id) / "workflow.log"
        payload = "0123456789" * 100  # 1_000 bytes
        workflow_log.write_text(payload, encoding="utf-8")

        original_read_bytes = Path.read_bytes

        def _fail_read_bytes(self: Path) -> bytes:
            if self == workflow_log:
                raise AssertionError(
                    "read_logs must not call Path.read_bytes on the "
                    "workflow log — it should seek to after_offset "
                    "and read only new bytes."
                )
            return original_read_bytes(self)

        monkeypatch.setattr(Path, "read_bytes", _fail_read_bytes)

        svc = _make_service(repo, FakeGraph())
        chunks = svc.read_logs(wf_id, after_offset=990)
        assert len(chunks) == 1
        assert chunks[0].offset == 990
        assert chunks[0].content == payload[990:]

    def test_returns_empty_chunk_when_file_exists_but_empty(self, temp_db, repo):
        """Preserve the historical contract: a zero-byte log with
        ``after_offset=0`` returns one empty chunk so callers can
        distinguish 'log created, no output yet' from 'no log file'."""
        wf_id = repo.create_workflow(ticket_key="AOS-9")
        workflow_log = workflow_logs_dir(wf_id) / "workflow.log"
        workflow_log.write_bytes(b"")

        svc = _make_service(repo, FakeGraph())
        chunks = svc.read_logs(wf_id)
        assert len(chunks) == 1
        assert chunks[0].content == ""
        assert chunks[0].offset == 0

    def test_returns_empty_list_when_caller_caught_up(self, temp_db, repo):
        """When ``after_offset >= size`` and the caller has already read
        past the current end of the file, ``read_logs`` returns ``[]``
        rather than an empty chunk."""
        wf_id = repo.create_workflow(ticket_key="AOS-10")
        workflow_log = workflow_logs_dir(wf_id) / "workflow.log"
        workflow_log.write_text("hello", encoding="utf-8")

        svc = _make_service(repo, FakeGraph())
        assert svc.read_logs(wf_id, after_offset=5) == []
        assert svc.read_logs(wf_id, after_offset=100) == []


# ---------------------------------------------------------------------------
# History & stream_events
# ---------------------------------------------------------------------------


class TestHistoryAndEvents:
    def _build_history(self) -> List[_FakeStateSnapshot]:
        # newest-first ordering (matches langgraph)
        return [
            _FakeStateSnapshot(
                metadata={"step": 2},
                tasks=[_FakeTask("generate_code", result={"summary": "ok"})],
            ),
            _FakeStateSnapshot(
                metadata={"step": 1},
                tasks=[_FakeTask("work_planner", result={"work_plan": {}})],
            ),
            _FakeStateSnapshot(
                metadata={"step": -1},  # synthetic input step — must be skipped
                tasks=[_FakeTask("__start__")],
            ),
        ]

    def test_get_history_skips_input_and_orders_chronologically(self, temp_db, repo):
        graph = FakeGraph(history=self._build_history())
        svc = _make_service(repo, graph)
        history = svc.get_history("wf-x")
        assert [(h.step, h.node) for h in history] == [
            (1, "work_planner"),
            (2, "generate_code"),
        ]
        assert history[0].outcome == "ok"
        assert history[0].result_keys == ["work_plan"]

    def test_get_history_marks_error_and_interrupt(self, temp_db, repo):
        history = [
            _FakeStateSnapshot(
                metadata={"step": 2},
                tasks=[_FakeTask("await_approval", interrupts=("paused",))],
            ),
            _FakeStateSnapshot(
                metadata={"step": 1},
                tasks=[_FakeTask("work_planner", error="boom")],
            ),
        ]
        graph = FakeGraph(history=history)
        svc = _make_service(repo, graph)
        result = svc.get_history("wf-x")
        assert result[0].outcome == "error" and result[0].error == "boom"
        assert result[1].outcome == "interrupted"

    def test_stream_events_yields_sequential_events(self, temp_db, repo):
        graph = FakeGraph(history=self._build_history())
        svc = _make_service(repo, graph)
        events = list(svc.stream_events("wf-x"))
        # 3 historical states × 1 task each = 3 events, replayed in
        # chronological order: the synthetic __start__ task first (no
        # result → "node_start"), then the two real nodes that produced
        # results.
        assert [e.seq for e in events] == [1, 2, 3]
        assert [e.kind for e in events] == ["node_start", "node_end", "node_end"]
        assert [e.node for e in events] == ["__start__", "work_planner", "generate_code"]
        assert all(isinstance(e, WorkflowEvent) for e in events)

    def test_stream_events_respects_after_seq(self, temp_db, repo):
        graph = FakeGraph(history=self._build_history())
        svc = _make_service(repo, graph)
        events = list(svc.stream_events("wf-x", after_seq=2))
        assert [e.seq for e in events] == [3]


# ---------------------------------------------------------------------------
# Admin / status mutations
# ---------------------------------------------------------------------------


class TestAdminMutations:
    def test_cancel_marks_workflow_cancelled(self, temp_db, repo):
        wf_id = repo.create_workflow(ticket_key="AOS-8")
        svc = _make_service(repo, FakeGraph())
        svc.cancel(wf_id, reason="testing", actor="tester")
        assert repo.get_workflow(wf_id)["status"] == WorkflowStatus.CANCELLED

    def test_mark_interrupted_is_noop_when_terminal(self, temp_db, repo):
        wf_id = repo.create_workflow(ticket_key="AOS-9")
        repo.update_status(wf_id, WorkflowStatus.COMPLETED)
        svc = _make_service(repo, FakeGraph())
        svc.mark_interrupted(wf_id, failed_node="generate_code")
        # Status stays COMPLETED (terminal); no-op.
        assert repo.get_workflow(wf_id)["status"] == WorkflowStatus.COMPLETED

    def test_mark_interrupted_sets_failed(self, temp_db, repo):
        wf_id = repo.create_workflow(ticket_key="AOS-10")
        repo.update_status(wf_id, WorkflowStatus.IN_PROGRESS)
        svc = _make_service(repo, FakeGraph())
        svc.mark_interrupted(wf_id, failed_node="generate_code")
        assert repo.get_workflow(wf_id)["status"] == WorkflowStatus.FAILED

    def test_clear_db_returns_counts(self, temp_db, repo):
        # The real clear_db requires the langgraph checkpoints table; use a
        # fake repo to keep the test focused on the service's delegation.
        class _StubRepo:
            def clear_db(self):
                return (7, 3)

        svc = LocalWorkflowService(
            repository=_StubRepo(),
            graph_factory=lambda: FakeGraph(),
        )
        assert svc.clear_db() == (7, 3)


# ---------------------------------------------------------------------------
# Graph-running operations
# ---------------------------------------------------------------------------


class TestStart:
    def test_start_dry_run_is_noop(self, temp_db, repo):
        svc = _make_service(repo, FakeGraph())
        result = svc.start(WorkflowStartRequest(ticket_key="AOS-20", dry_run=True))
        assert result.final_status == WorkflowStatus.PENDING
        assert repo.list_workflows() == []

    def test_start_uses_provided_workflow_id(self, temp_db, repo):
        wf_id = repo.create_workflow(ticket_key="AOS-21", status=WorkflowStatus.PENDING_APPROVAL)
        graph = FakeGraph(
            post_stream_state=_FakeStateSnapshot(
                values={"workflow_id": wf_id, "ticket_key": "AOS-21"}
            )
        )
        svc = _make_service(repo, graph)
        result = svc.start(WorkflowStartRequest(ticket_key="AOS-21", workflow_id=wf_id))
        # graph.stream was driven once with the start input
        assert graph._stream_called
        # status pulled from repo
        assert result.final_status == WorkflowStatus.PENDING_APPROVAL
        assert result.workflow_id == wf_id

    def test_start_writes_workflow_log(self, temp_db, repo):
        wf_id = repo.create_workflow(ticket_key="AOS-21", status=WorkflowStatus.PENDING_APPROVAL)

        class LoggingGraph(FakeGraph):
            def stream(self, *args, **kwargs):
                logger = logging.getLogger("tests.workflow_service")
                logger.setLevel(logging.INFO)
                logger.info("workflow %s ran", wf_id)
                return super().stream(*args, **kwargs)

        graph = LoggingGraph(
            post_stream_state=_FakeStateSnapshot(
                values={"workflow_id": wf_id, "ticket_key": "AOS-21"}
            )
        )
        svc = _make_service(repo, graph)
        svc.start(WorkflowStartRequest(ticket_key="AOS-21", workflow_id=wf_id))

        workflow_log = workflow_logs_dir(wf_id) / "workflow.log"
        assert workflow_log.exists()
        assert "workflow %s ran" % wf_id in workflow_log.read_text(encoding="utf-8")

    def test_start_completes_when_approval_already_present(self, temp_db, repo):
        wf_id = repo.create_workflow(ticket_key="AOS-22", status=WorkflowStatus.PENDING_APPROVAL)
        graph = FakeGraph(
            post_stream_state=_FakeStateSnapshot(
                values={
                    "workflow_id": wf_id,
                    "ticket_key": "AOS-22",
                    "approval_decision": "approved",
                    "code_generation_summary": {"status": "success", "pr_url": "http://pr/1"},
                }
            )
        )
        svc = _make_service(repo, graph)
        result = svc.start(WorkflowStartRequest(ticket_key="AOS-22", workflow_id=wf_id))
        assert result.final_status == WorkflowStatus.COMPLETED
        assert result.pr_url == "http://pr/1"
        assert repo.get_workflow(wf_id)["status"] == WorkflowStatus.COMPLETED

    def test_start_handles_graph_interrupt(self, temp_db, repo):
        wf_id = repo.create_workflow(ticket_key="AOS-23", status=WorkflowStatus.PENDING_APPROVAL)
        graph = FakeGraph(
            stream_raises=GraphInterrupt(()),
            state=_FakeStateSnapshot(values={"workflow_id": wf_id}),
            post_stream_state=_FakeStateSnapshot(values={"workflow_id": wf_id}),
        )
        svc = _make_service(repo, graph)
        result = svc.start(WorkflowStartRequest(ticket_key="AOS-23", workflow_id=wf_id))
        assert result.interrupted is True
        # Status comes from DB (PENDING_APPROVAL is what the await_approval
        # node would have set today).
        assert result.final_status == WorkflowStatus.PENDING_APPROVAL


class TestResumeOperations:
    def test_approve_plan_sets_pending_pr_approval_on_success(self, temp_db, repo):
        wf_id = repo.create_workflow(ticket_key="AOS-30", status=WorkflowStatus.PENDING_APPROVAL)
        graph = FakeGraph(
            post_stream_state=_FakeStateSnapshot(
                values={
                    "workflow_id": wf_id,
                    "ticket_key": "AOS-30",
                    "code_generation_summary": {
                        "status": "success",
                        "pr_url": "http://pr/30",
                    },
                }
            )
        )
        svc = _make_service(repo, graph)
        result = svc.approve_plan(wf_id)
        # The first stream call carries the resume Command for approval.
        cmd = graph.stream_calls[0][0][0]
        assert isinstance(cmd, Command)
        assert result.final_status == WorkflowStatus.PENDING_PR_APPROVAL
        assert result.pr_url == "http://pr/30"

    def test_approve_plan_marks_failed_when_execution_fails(self, temp_db, repo):
        wf_id = repo.create_workflow(ticket_key="AOS-31", status=WorkflowStatus.PENDING_APPROVAL)
        graph = FakeGraph(
            post_stream_state=_FakeStateSnapshot(
                values={
                    "workflow_id": wf_id,
                    "ticket_key": "AOS-31",
                    "code_generation_summary": {"status": "error", "error": "build broke"},
                }
            )
        )
        svc = _make_service(repo, graph)
        result = svc.approve_plan(wf_id)
        assert result.final_status == WorkflowStatus.FAILED

    def test_reject_plan_drives_graph_with_reason(self, temp_db, repo):
        wf_id = repo.create_workflow(ticket_key="AOS-32", status=WorkflowStatus.PENDING_APPROVAL)
        graph = FakeGraph(
            post_stream_state=_FakeStateSnapshot(
                values={"workflow_id": wf_id, "ticket_key": "AOS-32"}
            )
        )
        svc = _make_service(repo, graph)
        svc.reject_plan(wf_id, reason="scope creep")
        cmd = graph.stream_calls[0][0][0]
        assert isinstance(cmd, Command)

    def test_submit_clarification_passes_answers(self, temp_db, repo):
        wf_id = repo.create_workflow(
            ticket_key="AOS-33",
            status=WorkflowStatus.PENDING_WORKPLAN_CLARIFICATION,
        )
        answers = [{"concern": "a", "answer": "b"}]
        graph = FakeGraph(
            post_stream_state=_FakeStateSnapshot(
                values={"workflow_id": wf_id, "ticket_key": "AOS-33"}
            )
        )
        svc = _make_service(repo, graph)
        svc.submit_clarification(wf_id, answers)
        cmd = graph.stream_calls[0][0][0]
        assert isinstance(cmd, Command)

    def test_approve_pr_sets_completed(self, temp_db, repo):
        wf_id = repo.create_workflow(ticket_key="AOS-34", status=WorkflowStatus.PENDING_PR_APPROVAL)
        graph = FakeGraph(
            post_stream_state=_FakeStateSnapshot(
                values={"workflow_id": wf_id, "ticket_key": "AOS-34"}
            )
        )
        svc = _make_service(repo, graph)
        result = svc.approve_pr(wf_id)
        assert result.final_status == WorkflowStatus.COMPLETED
        assert repo.get_workflow(wf_id)["status"] == WorkflowStatus.COMPLETED

    def test_reject_pr_sets_rejected(self, temp_db, repo):
        wf_id = repo.create_workflow(ticket_key="AOS-35", status=WorkflowStatus.PENDING_PR_APPROVAL)
        graph = FakeGraph(
            post_stream_state=_FakeStateSnapshot(
                values={"workflow_id": wf_id, "ticket_key": "AOS-35"}
            )
        )
        svc = _make_service(repo, graph)
        result = svc.reject_pr(wf_id, reason="missing tests")
        assert result.final_status == WorkflowStatus.REJECTED
        assert repo.get_workflow(wf_id)["status"] == WorkflowStatus.REJECTED

    def test_comment_pr_passes_comments_payload(self, temp_db, repo):
        wf_id = repo.create_workflow(ticket_key="AOS-36", status=WorkflowStatus.PENDING_PR_APPROVAL)
        graph = FakeGraph(
            post_stream_state=_FakeStateSnapshot(
                values={"workflow_id": wf_id, "ticket_key": "AOS-36"}
            )
        )
        svc = _make_service(repo, graph)
        svc.comment_pr(wf_id, "please fix typo")
        cmd = graph.stream_calls[0][0][0]
        assert isinstance(cmd, Command)


class TestRetry:
    def test_retry_raises_when_workflow_missing(self, temp_db, repo):
        svc = _make_service(repo, FakeGraph())
        with pytest.raises(ValueError, match="not found"):
            svc.retry("missing")

    def test_retry_raises_when_not_retryable(self, temp_db, repo):
        wf_id = repo.create_workflow(ticket_key="AOS-40", status=WorkflowStatus.COMPLETED)
        svc = _make_service(repo, FakeGraph())
        with pytest.raises(ValueError, match="not retryable"):
            svc.retry(wf_id)

    def test_retry_rewinds_and_marks_completed_on_success(self, temp_db, repo):
        wf_id = repo.create_workflow(ticket_key="AOS-41", status=WorkflowStatus.FAILED)
        # initial state advertises the failed_node so retry can resolve it.
        initial = _FakeStateSnapshot(
            values={"failed_node": "generate_code"},
            next_nodes=("generate_code",),
        )
        # state_history needs a snapshot where "generate_code" is next
        history = [
            _FakeStateSnapshot(
                values={},
                next_nodes=("generate_code",),
                config={"configurable": {"thread_id": wf_id, "checkpoint_id": "ck-1"}},
            )
        ]
        post = _FakeStateSnapshot(
            values={
                "workflow_id": wf_id,
                "ticket_key": "AOS-41",
                "code_generation_summary": {"status": "success", "pr_url": "http://pr/41"},
            }
        )
        graph = FakeGraph(state=initial, history=history, post_stream_state=post)
        svc = _make_service(repo, graph)
        result = svc.retry(wf_id)
        assert result.final_status == WorkflowStatus.COMPLETED
        # increment_retry_count bumped retry_count to 1
        assert repo.get_workflow(wf_id)["retry_count"] == 1

    def test_retry_landing_at_pr_gate_marks_interrupted_not_completed(self, temp_db, repo):
        """AOS-280 regression at the service layer.

        A retry that re-runs ``generate_code`` successfully and then lands
        at the ``await_pr_approval`` gate must:

        * Return ``interrupted=True``.
        * Preserve the ``PENDING_PR_APPROVAL`` status written by the gate
          node (never overwrite to ``COMPLETED``).
        * Skip the retry post_processor entirely.

        We simulate the gate's DB write by having ``FakeGraph.stream``
        update the row to ``PENDING_PR_APPROVAL`` mid-run, then terminate
        cleanly (as LangGraph ≥1.x does for ``interrupt()``).
        """
        wf_id = repo.create_workflow(
            ticket_key="AOS-280-svc",
            status=WorkflowStatus.FAILED,
        )

        initial = _FakeStateSnapshot(
            values={"failed_node": "generate_code"},
            next_nodes=("generate_code",),
        )
        history = [
            _FakeStateSnapshot(
                values={},
                next_nodes=("generate_code",),
                config={"configurable": {"thread_id": wf_id, "checkpoint_id": "ck-1"}},
            )
        ]
        # Post-stream state carries a successful code_generation_summary
        # AND the gate node's ``next=("await_pr_approval",)`` marker.
        post = _FakeStateSnapshot(
            values={
                "workflow_id": wf_id,
                "ticket_key": "AOS-280-svc",
                "code_generation_summary": {
                    "status": "success",
                    "pr_url": "http://pr/280",
                },
                # Critically: no pr_approval_decision yet — human hasn't
                # decided.
            },
            next_nodes=("await_pr_approval",),
        )

        class GateWritingGraph(FakeGraph):
            """FakeGraph whose ``stream`` also performs the gate node's DB
            write, mirroring what ``await_pr_approval`` does immediately
            before its ``interrupt()`` call in production."""

            def stream(self, *args, **kwargs):
                # Simulate the gate node updating the row before pausing.
                repo.update_status(
                    wf_id,
                    WorkflowStatus.PENDING_PR_APPROVAL,
                    actor="await_pr_approval",
                    reason="Awaiting PR review",
                )
                return super().stream(*args, **kwargs)

        graph = GateWritingGraph(state=initial, history=history, post_stream_state=post)
        svc = _make_service(repo, graph)
        result = svc.retry(wf_id)

        assert result.interrupted is True
        assert result.final_status == WorkflowStatus.PENDING_PR_APPROVAL
        # The DB row must remain at the gate — NOT overwritten to COMPLETED.
        assert repo.get_workflow(wf_id)["status"] == WorkflowStatus.PENDING_PR_APPROVAL
        # pr_url should surface for the CLI banner.
        assert result.pr_url == "http://pr/280"
        # retry_count was still bumped.
        assert repo.get_workflow(wf_id)["retry_count"] == 1


class TestInterruptDetection:
    """Unified interrupt-detection contract for _run_graph.

    Two prongs must both be honoured:
    * Legacy path: GraphInterrupt raised by the stream.
    * Event path: stream terminates cleanly but the gate node has written
      a PENDING_* status to the DB (LangGraph ≥1.x behaviour).
    """

    def test_event_path_pending_pr_approval_transition_is_interrupted(self, temp_db, repo):
        """Row transitioning from a non-paused status into ``PENDING_PR_APPROVAL``
        during a run is treated as ``interrupted=True`` even though no
        ``GraphInterrupt`` was raised."""
        wf_id = repo.create_workflow(
            ticket_key="AOS-280-detect",
            status=WorkflowStatus.PENDING_APPROVAL,
        )

        class GateWritingGraph(FakeGraph):
            def stream(self, *args, **kwargs):
                repo.update_status(
                    wf_id,
                    WorkflowStatus.PENDING_PR_APPROVAL,
                    actor="await_pr_approval",
                    reason="Awaiting PR review",
                )
                return super().stream(*args, **kwargs)

        graph = GateWritingGraph(
            post_stream_state=_FakeStateSnapshot(
                values={"workflow_id": wf_id, "ticket_key": "AOS-280-detect"}
            )
        )
        svc = _make_service(repo, graph)
        result = svc.approve_plan(wf_id)

        assert result.interrupted is True
        assert result.final_status == WorkflowStatus.PENDING_PR_APPROVAL

    def test_event_path_no_status_change_is_not_interrupted(self, temp_db, repo):
        """A row that stays at the same paused status across a run (fake
        stream fires no gate node) must fall through to post_process — this
        guards the invariant used by existing FakeGraph tests where a stream
        that does nothing should not spuriously signal interrupt."""
        wf_id = repo.create_workflow(
            ticket_key="AOS-280-noop",
            status=WorkflowStatus.PENDING_APPROVAL,
        )
        graph = FakeGraph(
            post_stream_state=_FakeStateSnapshot(
                values={
                    "workflow_id": wf_id,
                    "ticket_key": "AOS-280-noop",
                    "code_generation_summary": {
                        "status": "success",
                        "pr_url": "http://pr/noop",
                    },
                }
            )
        )
        svc = _make_service(repo, graph)
        result = svc.approve_plan(wf_id)

        # approve_plan's post_process is invoked; it maps success to
        # PENDING_PR_APPROVAL (the "graph paused at PR gate" mapping).
        assert result.interrupted is False
        assert result.final_status == WorkflowStatus.PENDING_PR_APPROVAL
