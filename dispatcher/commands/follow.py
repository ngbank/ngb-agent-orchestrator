"""Lifecycle event follower for fire-and-forget HTTP submissions.

When the dispatcher targets a remote orchestrator (``ORCHESTRATOR_MODE=remote``),
graph-running endpoints return ``202 Accepted`` immediately and run the actual
graph drive on a background worker.  The follower attaches to the server's
``/workflows/{id}/events`` SSE stream so the operator sees the same kind of
progress feedback they would have gotten when the call was synchronous.

The follower is intentionally a no-op for the in-process
:class:`LocalWorkflowService` — local runs already execute synchronously in
the caller's thread, so there is nothing to ``follow``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Iterable, Optional, Set

import click

from state.workflow_status import WorkflowStatus

if TYPE_CHECKING:
    from orchestrator.workflow_service import WorkflowService
    from orchestrator.workflow_service.dtos import WorkflowEvent, WorkflowRunResult


# Terminal event kinds emitted by the server's lifecycle stream.  When one
# of these arrives the workflow is no longer in flight and the follower
# returns.  ``interrupt`` is "soft-terminal": the graph paused at a human
# gate (await_approval, await_pr_approval, …) and the dispatcher should
# stop streaming so the user can act.
_TERMINAL_KINDS: Set[str] = {"completed", "failed", "cancelled", "interrupt"}

# Workflow statuses that mean "graph is paused at a human gate".  Used to
# synthesise the ``interrupted`` flag on the refreshed WorkflowRunResult
# the post-follow handlers consume.
_PAUSED_STATUSES = {
    WorkflowStatus.PENDING_APPROVAL,
    WorkflowStatus.PENDING_PR_APPROVAL,
}


def is_remote_service(service: "WorkflowService") -> bool:
    """Return True when ``service`` is the HTTP-backed transport.

    The check is intentionally narrow: only the real
    :class:`HttpWorkflowService` triggers auto-follow.  Test fakes and the
    local implementation are treated as in-process and skip the follower.
    """
    # Imported lazily to avoid pulling httpx into ``--help`` invocations.
    from orchestrator.workflow_service.http_workflow_service import HttpWorkflowService

    return isinstance(service, HttpWorkflowService)


def _format_event(event: "WorkflowEvent") -> str:
    node = f" {event.node}" if event.node else ""
    if event.kind == "node_start":
        return f"▶️  [{event.seq:>4}] start{node}"
    if event.kind == "node_end":
        return f"✅ [{event.seq:>4}] end  {node}"
    if event.kind == "interrupt":
        return f"⏸️  [{event.seq:>4}] pause{node} — awaiting input"
    if event.kind == "completed":
        return f"🎉 [{event.seq:>4}] completed"
    if event.kind == "failed":
        reason = ""
        if event.data and isinstance(event.data, dict):
            err = event.data.get("error") or event.data.get("reason")
            if err:
                reason = f" — {err}"
        return f"❌ [{event.seq:>4}] failed{node}{reason}"
    if event.kind == "cancelled":
        return f"🛑 [{event.seq:>4}] cancelled"
    return f"·  [{event.seq:>4}] {event.kind}{node}"


def follow_workflow(
    service: "WorkflowService",
    workflow_id: str,
    *,
    after_seq: int = 0,
) -> "Optional[WorkflowEvent]":
    """Stream lifecycle events for ``workflow_id`` until a terminal one arrives.

    Returns the final terminal event (or ``None`` if the stream ended without
    emitting one — e.g. server disconnect).  Returns immediately and silently
    when ``service`` is not a remote transport.

    ``KeyboardInterrupt`` (Ctrl-C) detaches from the stream without affecting
    the server-side workflow — the operator can re-attach later.
    """
    if not is_remote_service(service):
        return None

    click.echo(f"📡 Following workflow {workflow_id} (Ctrl-C to detach)…")
    last: "Optional[WorkflowEvent]" = None
    try:
        events: Iterable["WorkflowEvent"] = service.stream_events(workflow_id, after_seq=after_seq)
        for event in events:
            click.echo(_format_event(event))
            last = event
            if event.kind in _TERMINAL_KINDS:
                return event
        return last
    except KeyboardInterrupt:
        click.echo(
            "\nℹ️  Detached from event stream — the workflow continues " "on the server.",
            err=True,
        )
        return last


def submit_and_follow(
    service: "WorkflowService",
    op_fn: Callable[..., "WorkflowRunResult"],
    *args: Any,
    workflow_id_hint: Optional[str] = None,
    detach: bool = False,
    **kwargs: Any,
) -> "WorkflowRunResult":
    """Invoke ``op_fn`` and, in remote mode, follow the workflow to completion.

    In local mode (``LocalWorkflowService``) this is equivalent to calling
    ``op_fn(*args, **kwargs)`` directly — the graph already runs
    synchronously in the caller's thread.

    In remote mode the HTTP call returns ``202 Accepted`` with a snapshot
    of the workflow row.  We then subscribe to the lifecycle SSE stream and
    (when it terminates) re-read the workflow detail to produce a
    ``WorkflowRunResult`` whose ``final_status`` / ``execution_summary`` /
    ``pr_url`` match the post-run state — keeping the existing handler
    post-processing (status banners, comment-on-ticket) unchanged.

    Pass ``detach=True`` to skip the follower (and return the initial 202
    snapshot) — useful for scripted submissions that just need the id.
    """
    from orchestrator.workflow_service.dtos import WorkflowRunResult

    initial = op_fn(*args, **kwargs)
    if detach or not is_remote_service(service):
        return initial

    wf_id = initial.workflow_id or workflow_id_hint or ""
    if not wf_id:
        return initial

    follow_workflow(service, wf_id)

    detail = service.get(wf_id)
    if detail is None:
        return initial

    return WorkflowRunResult(
        workflow_id=wf_id,
        ticket_key=detail.ticket_key,
        final_status=detail.status,
        interrupted=detail.status in _PAUSED_STATUSES,
        execution_summary=detail.execution_summary,
        pr_url=detail.pr_url,
    )


__all__ = ["follow_workflow", "is_remote_service", "submit_and_follow"]
