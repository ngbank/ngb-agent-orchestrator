"""LocalWorkflowService — in-process implementation of WorkflowService.

Thin wrapper over the existing collaborators:

* :class:`state.workflow_repository.WorkflowRepository` for persistence.
* The langgraph orchestrator graph (constructed via a factory) for execution.
* :func:`orchestrator.utils.log_path` for reading captured stage logs.

This class is a one-to-one shim — it does not change any existing behaviour.
The DTOs it returns mirror what the dispatcher already computes inline; this
ticket only relocates that computation behind a Protocol so call sites can
later be swapped to a remote transport (Stage B) without further refactoring.

Operational notes for callers:

* Graph-running methods (``start``, ``approve_plan`` …) **do not** print, post
  JIRA comments, or catch ``KeyboardInterrupt``.  They return a
  ``WorkflowRunResult`` describing the outcome; UX concerns live in the
  caller.
* ``stream_events`` is replay-only — it walks ``graph.get_state_history`` once
  and stops.  Live streaming arrives in Stage C.
* Constructor takes a ``graph_factory`` so tests can inject a FakeGraph
  without paying the langgraph startup cost.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional

from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphInterrupt
from langgraph.types import Command

from orchestrator.retry import prepare_retry
from orchestrator.utils import log_path
from state.workflow_repository import WorkflowRepository
from state.workflow_status import WorkflowStatus

from .dtos import (
    WorkflowAuditEntry,
    WorkflowDetail,
    WorkflowEvent,
    WorkflowHistoryEntry,
    WorkflowLogChunk,
    WorkflowRunResult,
    WorkflowStartRequest,
    WorkflowSummary,
)

# Mapping from graph-resume payloads to the WorkflowStatus the service applies
# after the graph returns.  Kept here (not on the Protocol) because it is a
# concrete behavioural detail of the local implementation.
_PR_DECISION_FINAL_STATUS: Dict[str, WorkflowStatus] = {
    "approved": WorkflowStatus.COMPLETED,
    "rejected": WorkflowStatus.REJECTED,
}


def _make_thread_config(workflow_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": workflow_id}}


def _default_graph_factory() -> Any:
    """Lazy import + OTel bootstrap, mirroring dispatcher.commands.common.build_orchestrator."""
    from orchestrator.builder import build_orchestrator as _build_orchestrator
    from otel import setup_tracing

    setup_tracing()
    return _build_orchestrator()


def _drive_graph_stream(
    graph: Any,
    graph_input: Any,
    *,
    workflow_id: str,
    ticket_key: str,
    thread_config: RunnableConfig,
) -> None:
    """Set OTel workflow context and drive the graph stream to completion.

    Mirrors :func:`dispatcher.commands.common.run_graph_stream` so that this
    service produces identical instrumentation behaviour to the existing CLI
    handlers.
    """
    from otel import instrument_graph_stream, set_workflow_context

    set_workflow_context(workflow_id=workflow_id, ticket_key=ticket_key)
    for _ in instrument_graph_stream(graph, graph_input, config=thread_config):
        # The stream is consumed for its side effects (state checkpointing +
        # OTel spans); the per-event payloads are not used here.
        pass


class LocalWorkflowService:
    """Default in-process WorkflowService.

    Construct with explicit collaborators in tests; rely on
    :func:`build_local_workflow_service` for production wiring.
    """

    def __init__(
        self,
        repository: WorkflowRepository,
        graph_factory: Callable[[], Any] = _default_graph_factory,
    ) -> None:
        self._repo = repository
        self._graph_factory = graph_factory

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, workflow_id: str) -> Optional[WorkflowDetail]:
        row = self._repo.get_workflow(workflow_id)
        if row is None:
            return None
        return _to_detail(row)

    def get_by_ticket(self, ticket_key: str) -> List[WorkflowSummary]:
        return [_to_summary(r) for r in self._repo.get_workflow_by_ticket(ticket_key)]

    def get_latest_retryable_by_ticket(self, ticket_key: str) -> Optional[WorkflowSummary]:
        row = self._repo.get_latest_retryable_workflow_by_ticket(ticket_key)
        return _to_summary(row) if row else None

    def list(
        self,
        ticket_key: Optional[str] = None,
        status: Optional[WorkflowStatus] = None,
        limit: int = 50,
    ) -> List[WorkflowSummary]:
        status_str = status.value if status is not None else None
        rows = self._repo.list_workflows(ticket_key=ticket_key, status=status_str, limit=limit)
        return [_to_summary(r) for r in rows]

    def get_history(self, workflow_id: str) -> List[WorkflowHistoryEntry]:
        graph = self._graph_factory()
        thread_config = _make_thread_config(workflow_id)
        # get_state_history returns newest-first; the CLI today reverses for
        # chronological order, so we do the same here.
        history = list(graph.get_state_history(thread_config))
        history.reverse()
        entries: List[WorkflowHistoryEntry] = []
        for state in history:
            step = (state.metadata or {}).get("step", -1)
            if step == -1:
                # Skip the synthetic input step — matches admin._handle_history.
                continue
            for task in state.tasks:
                if task.error:
                    outcome = "error"
                    error = str(task.error)
                    result_keys: List[str] = []
                elif task.interrupts:
                    outcome = "interrupted"
                    error = None
                    result_keys = []
                else:
                    outcome = "ok"
                    error = None
                    result_keys = list((task.result or {}).keys())
                entries.append(
                    WorkflowHistoryEntry(
                        step=int(step),
                        node=task.name,
                        outcome=outcome,
                        result_keys=result_keys,
                        error=error,
                    )
                )
        return entries

    def get_audit_log(self, workflow_id: str) -> List[WorkflowAuditEntry]:
        return [_to_audit(workflow_id, r) for r in self._repo.get_audit_log(workflow_id)]

    def read_logs(
        self,
        workflow_id: str,
        stage: Optional[str] = None,
        after_offset: int = 0,
    ) -> List[WorkflowLogChunk]:
        ticket_key: Optional[str] = None
        row = self._repo.get_workflow(workflow_id)
        if row is not None:
            ticket_key = row.get("ticket_key")
        stages = [stage] if stage else ["plan", "execute"]
        chunks: List[WorkflowLogChunk] = []
        for st in stages:
            path = log_path(workflow_id, st, ticket_key=ticket_key)
            if not path.exists():
                continue
            raw = path.read_bytes()
            size = len(raw)
            start = max(0, min(after_offset, size))
            if start >= size and after_offset > 0:
                # Caller is already caught up on this stage — skip emitting an
                # empty chunk so they can distinguish "no new bytes" from "no
                # log file yet" (the latter is also omitted above).
                continue
            content_bytes = raw[start:]
            try:
                content = content_bytes.decode("utf-8")
            except UnicodeDecodeError:
                content = content_bytes.decode("utf-8", errors="replace")
            chunks.append(
                WorkflowLogChunk(
                    workflow_id=workflow_id,
                    stage=st,
                    path=str(path),
                    content=content,
                    offset=start,
                )
            )
        return chunks

    def stream_events(
        self,
        workflow_id: str,
        after_seq: int = 0,
    ) -> Iterable[WorkflowEvent]:
        """Replay langgraph state history as transport-agnostic events.

        Each historical state contributes ``node_start`` events (one per
        pending task) plus a terminal ``node_end`` / ``interrupt`` / ``error``
        event when the task has completed.  Sequence numbers are assigned in
        chronological order; clients pass the last seen ``seq`` via
        ``after_seq`` to resume.
        """
        graph = self._graph_factory()
        thread_config = _make_thread_config(workflow_id)
        history = list(graph.get_state_history(thread_config))
        history.reverse()

        seq = 0
        for state in history:
            for task in state.tasks:
                if task.error:
                    kind = "failed"
                    data: Optional[Dict[str, Any]] = {"error": str(task.error)}
                elif task.interrupts:
                    kind = "interrupt"
                    data = None
                elif task.result:
                    kind = "node_end"
                    data = {"result_keys": list(task.result.keys())}
                else:
                    kind = "node_start"
                    data = None
                seq += 1
                if seq <= after_seq:
                    continue
                yield WorkflowEvent(
                    workflow_id=workflow_id,
                    seq=seq,
                    kind=kind,
                    node=task.name,
                    data=data,
                )

    # ------------------------------------------------------------------
    # Admin / status mutations
    # ------------------------------------------------------------------

    def cancel(
        self,
        workflow_id: str,
        reason: Optional[str] = None,
        actor: str = "system",
    ) -> None:
        self._repo.update_status(
            workflow_id,
            WorkflowStatus.CANCELLED,
            actor=actor,
            reason=reason or "Cancelled by user",
        )

    def mark_interrupted(
        self,
        workflow_id: str,
        failed_node: Optional[str] = None,
        actor: str = "system",
    ) -> None:
        workflow = self._repo.get_workflow(workflow_id)
        if workflow is None or workflow["status"].is_terminal():
            return
        self._repo.update_status(
            workflow_id,
            WorkflowStatus.FAILED,
            actor=actor,
            reason=(
                f"Interrupted by user (Ctrl-C) at node '{failed_node}'"
                if failed_node
                else "Interrupted by user (Ctrl-C)"
            ),
        )

    def mark_failed(
        self,
        workflow_id: str,
        reason: str,
        actor: str = "system",
    ) -> None:
        workflow = self._repo.get_workflow(workflow_id)
        if workflow is None or workflow["status"].is_terminal():
            return
        self._repo.update_status(
            workflow_id,
            WorkflowStatus.FAILED,
            actor=actor,
            reason=reason,
        )

    def clear_db(self) -> tuple[int, int]:
        return self._repo.clear_db()

    # ------------------------------------------------------------------
    # Graph-running operations
    # ------------------------------------------------------------------

    def prepare_start(self, request: WorkflowStartRequest) -> WorkflowStartRequest:
        """Reserve a workflow_id and persist a PENDING row.

        Server-side helper used by the HTTP transport's fire-and-forget
        dispatch: callers invoke ``prepare_start`` synchronously so
        ``GET /workflows/{id}`` reflects the new workflow before the
        background graph drive runs, then submit ``start(prepared)`` to a
        worker thread.  The graph's ``create_workflow_record`` node is
        idempotent and will reuse the row this method creates.

        Dry-run requests are returned unchanged with no DB write.
        """
        if request.dry_run:
            return request
        if request.workflow_id and self._repo.get_workflow(request.workflow_id) is not None:
            return request
        workflow_id = request.workflow_id or str(uuid.uuid4())
        self._repo.create_workflow(
            ticket_key=request.ticket_key,
            work_plan=None,
            status=WorkflowStatus.PENDING,
            workflow_id=workflow_id,
        )
        return WorkflowStartRequest(
            ticket_key=request.ticket_key,
            workflow_id=workflow_id,
            dry_run=request.dry_run,
        )

    def start(self, request: WorkflowStartRequest) -> WorkflowRunResult:
        # Dry-run is a no-op at the service layer; the CLI handles the
        # informational echoes today.  Returning a placeholder keeps the
        # protocol uniform without writing anything to the DB.
        if request.dry_run:
            return WorkflowRunResult(
                workflow_id=request.workflow_id or "",
                ticket_key=request.ticket_key,
                final_status=WorkflowStatus.PENDING,
            )

        workflow_id = request.workflow_id or str(uuid.uuid4())
        thread_config = _make_thread_config(workflow_id)
        graph_input = {
            "ticket_key": request.ticket_key,
            "dry_run": False,
            "workflow_id": workflow_id,
        }

        return self._run_graph(
            graph_input,
            workflow_id=workflow_id,
            ticket_key=request.ticket_key,
            thread_config=thread_config,
            post_process=self._post_process_start,
        )

    def approve_plan(self, workflow_id: str) -> WorkflowRunResult:
        ticket_key = self._ticket_for(workflow_id)
        thread_config = _make_thread_config(workflow_id)
        return self._run_graph(
            Command(resume={"decision": "approved"}),
            workflow_id=workflow_id,
            ticket_key=ticket_key,
            thread_config=thread_config,
            post_process=self._post_process_approve_plan,
        )

    def reject_plan(self, workflow_id: str, reason: Optional[str]) -> WorkflowRunResult:
        ticket_key = self._ticket_for(workflow_id)
        thread_config = _make_thread_config(workflow_id)
        return self._run_graph(
            Command(resume={"decision": "rejected", "reason": reason}),
            workflow_id=workflow_id,
            ticket_key=ticket_key,
            thread_config=thread_config,
        )

    def submit_clarification(
        self,
        workflow_id: str,
        answers: List[Dict[str, str]],
    ) -> WorkflowRunResult:
        ticket_key = self._ticket_for(workflow_id)
        thread_config = _make_thread_config(workflow_id)
        return self._run_graph(
            Command(resume={"answers": answers}),
            workflow_id=workflow_id,
            ticket_key=ticket_key,
            thread_config=thread_config,
        )

    def retry(self, workflow_id: str) -> WorkflowRunResult:
        workflow = self._repo.get_workflow(workflow_id)
        if workflow is None:
            raise ValueError(f"Workflow not found: {workflow_id}")
        if not workflow["status"].is_retryable():
            raise ValueError(
                f"Workflow {workflow_id} is not retryable (status: " f"{workflow['status'].value})"
            )

        ticket_key = workflow.get("ticket_key", "")
        thread_config = _make_thread_config(workflow_id)
        graph = self._graph_factory()
        current_state = graph.get_state(thread_config)
        failed_node = (current_state.values or {}).get("failed_node")
        if not failed_node:
            next_nodes = current_state.next or ()
            failed_node = next_nodes[0] if next_nodes else None
        if not failed_node:
            raise ValueError(
                f"Workflow {workflow_id} has no recorded failed_node and no "
                f"pending next node; cannot determine where to resume."
            )

        prepare_retry(graph, thread_config, failed_node)
        new_count = self._repo.increment_retry_count(workflow_id, actor="dispatcher")
        self._repo.update_status(
            workflow_id,
            WorkflowStatus.IN_PROGRESS,
            actor="dispatcher",
            reason=f"Retry attempt #{new_count} from failed_node '{failed_node}'",
        )

        return self._run_graph(
            None,
            workflow_id=workflow_id,
            ticket_key=ticket_key,
            thread_config=thread_config,
            post_process=self._post_process_retry,
            graph=graph,
        )

    def approve_pr(self, workflow_id: str) -> WorkflowRunResult:
        ticket_key = self._ticket_for(workflow_id)
        thread_config = _make_thread_config(workflow_id)
        return self._run_graph(
            Command(resume={"decision": "approved"}),
            workflow_id=workflow_id,
            ticket_key=ticket_key,
            thread_config=thread_config,
            post_process=self._post_process_pr_decision("approved"),
        )

    def comment_pr(self, workflow_id: str, comments: str) -> WorkflowRunResult:
        ticket_key = self._ticket_for(workflow_id)
        thread_config = _make_thread_config(workflow_id)
        return self._run_graph(
            Command(resume={"decision": "commented", "comments": comments}),
            workflow_id=workflow_id,
            ticket_key=ticket_key,
            thread_config=thread_config,
        )

    def reject_pr(self, workflow_id: str, reason: Optional[str]) -> WorkflowRunResult:
        ticket_key = self._ticket_for(workflow_id)
        thread_config = _make_thread_config(workflow_id)
        return self._run_graph(
            Command(resume={"decision": "rejected", "reason": reason}),
            workflow_id=workflow_id,
            ticket_key=ticket_key,
            thread_config=thread_config,
            post_process=self._post_process_pr_decision("rejected", reason=reason),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ticket_for(self, workflow_id: str) -> str:
        row = self._repo.get_workflow(workflow_id)
        return (row or {}).get("ticket_key", "") if row else ""

    def _run_graph(
        self,
        graph_input: Any,
        *,
        workflow_id: str,
        ticket_key: str,
        thread_config: RunnableConfig,
        post_process: Optional[
            Callable[[str, Optional[str], Dict[str, Any]], WorkflowRunResult]
        ] = None,
        graph: Optional[Any] = None,
    ) -> WorkflowRunResult:
        """Common run-and-resolve flow shared by every graph-running method.

        Returns a ``WorkflowRunResult`` reflecting the outcome.  Catches
        ``GraphInterrupt`` (sets ``interrupted=True``) but lets every other
        exception propagate so the caller can decide how to surface it
        (today the dispatcher prints, sets exit codes, and so on).
        """
        runnable: Any = graph if graph is not None else self._graph_factory()
        try:
            _drive_graph_stream(
                runnable,
                graph_input,
                workflow_id=workflow_id,
                ticket_key=ticket_key,
                thread_config=thread_config,
            )
        except GraphInterrupt:
            final_state = runnable.get_state(thread_config).values or {}
            row = self._repo.get_workflow(workflow_id)
            status = row["status"] if row else WorkflowStatus.PENDING
            execution_summary = final_state.get("execution_summary")
            return WorkflowRunResult(
                workflow_id=workflow_id,
                ticket_key=final_state.get("ticket_key") or ticket_key,
                final_status=status,
                interrupted=True,
                execution_summary=execution_summary if execution_summary else None,
                pr_url=(execution_summary or {}).get("pr_url"),
                failed_node=final_state.get("failed_node"),
                final_state=dict(final_state) if final_state else None,
            )

        final_state = runnable.get_state(thread_config).values or {}
        if post_process is None:
            row = self._repo.get_workflow(workflow_id)
            status = row["status"] if row else WorkflowStatus.PENDING
            execution_summary = final_state.get("execution_summary")
            return WorkflowRunResult(
                workflow_id=workflow_id,
                ticket_key=final_state.get("ticket_key") or ticket_key,
                final_status=status,
                error=final_state.get("error"),
                execution_summary=execution_summary if execution_summary else None,
                pr_url=(execution_summary or {}).get("pr_url"),
                failed_node=final_state.get("failed_node"),
                final_state=dict(final_state) if final_state else None,
            )
        return post_process(workflow_id, ticket_key, dict(final_state))

    # -- post-processors ----------------------------------------------------
    #
    # Each post-processor takes (workflow_id, ticket_key, final_state) and
    # produces a WorkflowRunResult, applying any status updates the existing
    # CLI handler would apply.  Keeping them as methods lets us share access
    # to ``self._repo`` without leaking implementation details into the
    # public protocol.

    def _post_process_start(
        self,
        workflow_id: str,
        ticket_key: Optional[str],
        final_state: Dict[str, Any],
    ) -> WorkflowRunResult:
        wf_id = final_state.get("workflow_id", workflow_id)
        ticket = final_state.get("ticket_key") or ticket_key
        error = final_state.get("error")

        # Mirror dispatcher.commands.run_workflow._handle_run: COMPLETED is
        # only set when the graph ran past approval (rare on first start).
        if error is None and final_state.get("approval_decision") == "approved":
            self._repo.update_status(
                wf_id,
                WorkflowStatus.COMPLETED,
                actor="dispatcher",
                reason="All stages completed successfully",
            )

        row = self._repo.get_workflow(wf_id)
        status = row["status"] if row else WorkflowStatus.PENDING
        execution_summary = final_state.get("execution_summary")
        return WorkflowRunResult(
            workflow_id=wf_id,
            ticket_key=ticket,
            final_status=status,
            error=error,
            execution_summary=execution_summary if execution_summary else None,
            pr_url=(execution_summary or {}).get("pr_url"),
            failed_node=final_state.get("failed_node"),
            final_state=final_state,
        )

    def _post_process_approve_plan(
        self,
        workflow_id: str,
        ticket_key: Optional[str],
        final_state: Dict[str, Any],
    ) -> WorkflowRunResult:
        wf_id = final_state.get("workflow_id", workflow_id)
        ticket = final_state.get("ticket_key") or ticket_key
        execution_summary = final_state.get("execution_summary") or {}
        exec_status = execution_summary.get("status", "")
        actor = "dispatcher"

        if exec_status in ("success", "partial"):
            self._repo.update_status(
                wf_id,
                WorkflowStatus.PENDING_PR_APPROVAL,
                actor=actor,
                reason="Execution completed — awaiting PR approval",
            )
        else:
            # No success/partial → either an explicit failure summary, or no
            # summary at all (execute_plan never wrote one).  Both must mark
            # the workflow FAILED so the dispatcher doesn't leave it stuck in
            # PENDING_APPROVAL after a degenerate run.
            self._repo.update_status(
                wf_id,
                WorkflowStatus.FAILED,
                actor=actor,
                reason=(
                    f"Execution failed: "
                    f"{execution_summary.get('error', exec_status or 'no execution summary')}"
                ),
            )

        row = self._repo.get_workflow(wf_id)
        status = row["status"] if row else WorkflowStatus.PENDING
        return WorkflowRunResult(
            workflow_id=wf_id,
            ticket_key=ticket,
            final_status=status,
            execution_summary=execution_summary if execution_summary else None,
            pr_url=execution_summary.get("pr_url") if execution_summary else None,
            failed_node=final_state.get("failed_node"),
            final_state=final_state,
        )

    def _post_process_retry(
        self,
        workflow_id: str,
        ticket_key: Optional[str],
        final_state: Dict[str, Any],
    ) -> WorkflowRunResult:
        wf_id = workflow_id
        ticket = final_state.get("ticket_key") or ticket_key
        execution_summary = final_state.get("execution_summary") or {}
        exec_status = execution_summary.get("status", "")
        new_failed_node = final_state.get("failed_node")
        graph_error = final_state.get("error")
        actor = "dispatcher"

        if execution_summary and exec_status in ("success", "partial"):
            self._repo.update_status(
                wf_id,
                WorkflowStatus.COMPLETED,
                actor=actor,
                reason="All stages completed successfully after retry",
            )
        elif execution_summary or graph_error or new_failed_node:
            self._repo.update_status(
                wf_id,
                WorkflowStatus.FAILED,
                actor=actor,
                reason=(
                    f"Retry failed: "
                    f"{execution_summary.get('error') or graph_error or new_failed_node or 'unknown'}"  # noqa: E501
                ),
            )

        row = self._repo.get_workflow(wf_id)
        status = row["status"] if row else WorkflowStatus.PENDING
        return WorkflowRunResult(
            workflow_id=wf_id,
            ticket_key=ticket,
            final_status=status,
            error=graph_error,
            execution_summary=execution_summary if execution_summary else None,
            pr_url=execution_summary.get("pr_url") if execution_summary else None,
            failed_node=new_failed_node,
            final_state=final_state,
        )

    def _post_process_pr_decision(
        self,
        decision: str,
        *,
        reason: Optional[str] = None,
    ) -> Callable[[str, Optional[str], Dict[str, Any]], WorkflowRunResult]:
        target_status = _PR_DECISION_FINAL_STATUS[decision]
        update_reason = (
            "PR approved by reviewer"
            if decision == "approved"
            else (reason or "PR rejected by reviewer")
        )

        def _apply(
            workflow_id: str,
            ticket_key: Optional[str],
            final_state: Dict[str, Any],
        ) -> WorkflowRunResult:
            wf_id = final_state.get("workflow_id", workflow_id)
            ticket = final_state.get("ticket_key") or ticket_key
            self._repo.update_status(
                wf_id,
                target_status,
                actor="dispatcher",
                reason=update_reason,
            )
            execution_summary = final_state.get("execution_summary")
            return WorkflowRunResult(
                workflow_id=wf_id,
                ticket_key=ticket,
                final_status=target_status,
                execution_summary=execution_summary if execution_summary else None,
                pr_url=(execution_summary or {}).get("pr_url"),
                final_state=final_state,
            )

        return _apply


# ---------------------------------------------------------------------------
# Mapping helpers (row dict -> DTO)
# ---------------------------------------------------------------------------


def _to_summary(row: Dict[str, Any]) -> WorkflowSummary:
    return WorkflowSummary(
        id=row["id"],
        ticket_key=row["ticket_key"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        pr_url=row.get("pr_url"),
    )


def _to_detail(row: Dict[str, Any]) -> WorkflowDetail:
    usage = row.get("usage_summary")
    if isinstance(usage, str):
        import json

        try:
            usage = json.loads(usage)
        except (json.JSONDecodeError, TypeError):
            usage = {}
    return WorkflowDetail(
        id=row["id"],
        ticket_key=row["ticket_key"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        pr_url=row.get("pr_url"),
        work_plan=row.get("work_plan"),
        execution_summary=row.get("execution_summary"),
        clarification_history=row.get("clarification_history") or [],
        pr_comments=row.get("pr_comments"),
        usage_summary=usage or {},
        retry_count=int(row.get("retry_count") or 0),
    )


def _to_audit(workflow_id: str, row: Dict[str, Any]) -> WorkflowAuditEntry:
    return WorkflowAuditEntry(
        workflow_id=workflow_id,
        actor=row.get("actor", ""),
        action=row.get("action", ""),
        timestamp=row.get("timestamp", ""),
        details=row.get("details"),
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_local_workflow_service(
    repository: Optional[WorkflowRepository] = None,
    graph_factory: Callable[[], Any] = _default_graph_factory,
) -> LocalWorkflowService:
    """Return a ``LocalWorkflowService`` wired with the default singletons.

    Pass ``repository`` to inject a fake; otherwise the default SQLite
    singleton is used.  Pass ``graph_factory`` to inject a fake graph; the
    default lazily builds the real orchestrator graph on first use.
    """
    if repository is None:
        from state.sqlite_workflow_repository import get_repository

        repository = get_repository()
    return LocalWorkflowService(repository=repository, graph_factory=graph_factory)
