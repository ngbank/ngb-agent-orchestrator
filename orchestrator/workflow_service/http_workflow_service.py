"""HttpWorkflowService — remote :class:`WorkflowService` implementation.

A thin HTTP client that targets the FastAPI orchestrator server.  Every
method satisfies the same :class:`WorkflowService` Protocol as
:class:`LocalWorkflowService` so the dispatcher CLI / TUI can switch
between in-process and remote execution by configuration only.

Coverage:

* REST: ``start``, ``get``, ``list``, ``get_by_ticket``,
  ``get_latest_retryable_by_ticket``, ``cancel``, ``approve_plan``,
  ``reject_plan``, ``submit_clarification``, ``retry``, ``approve_pr``,
  ``reject_pr``, ``comment_pr``, ``get_history``, ``get_audit_log``,
  ``mark_interrupted``, ``clear_db``.
* SSE: ``read_logs`` (drain once), ``stream_events`` (consume +
  auto-reconnect).

Operational notes:

* OTel ``traceparent`` / ``tracestate`` are injected on every request so
  per-call spans stitch into the active dispatcher trace.
* A bearer token (when configured) is sent as ``Authorization: Bearer ...``.
* The :class:`HttpWorkflowService` owns an :class:`httpx.Client` by default
  and exposes :meth:`close` for explicit teardown; tests inject their own
  client via :class:`httpx.ASGITransport` to avoid real network I/O.
* :class:`RemoteOperationNotSupported` remains exported for backward
  compatibility but is no longer raised by any method.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, Iterator, List, Optional

import httpx

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

logger = logging.getLogger(__name__)


# Read timeout used while draining the one-shot log SSE stream.  Must be
# larger than the server's ``LOG_POLL_INTERVAL_S`` (0.25s) so the first poll
# completes, and small enough that a snapshot caller does not block waiting
# for the next poll cycle.  Module-level so tests can monkeypatch it.
LOG_SNAPSHOT_READ_TIMEOUT_S: float = 1.0

# Default connect/write timeouts for one-shot REST calls.
DEFAULT_TIMEOUT_S: float = 30.0

# How long to wait between reconnect attempts in ``stream_events``.
RECONNECT_BACKOFF_S: float = 0.5

# Maximum reconnect attempts before giving up on ``stream_events``.  Set
# generously since each successful event resets the counter.
MAX_RECONNECT_ATTEMPTS: int = 5


# ---------------------------------------------------------------------------
# Public exceptions
# ---------------------------------------------------------------------------


class RemoteOperationNotSupported(NotImplementedError):
    """Raised when a Protocol method has no server-side endpoint yet.

    Distinct from a plain ``NotImplementedError`` so callers can distinguish
    "the server cannot do this right now" from "the implementation is
    incomplete" if they ever need to.
    """


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------


class HttpWorkflowService:
    """:class:`WorkflowService` backed by HTTP calls to the orchestrator server.

    Construct via :func:`build_http_workflow_service` for production wiring;
    pass an explicit ``client`` in tests (typically backed by
    :class:`httpx.ASGITransport`).
    """

    def __init__(
        self,
        base_url: str,
        *,
        token: Optional[str] = None,
        client: Optional[httpx.Client] = None,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            self._client = httpx.Client(
                base_url=self._base_url,
                timeout=httpx.Timeout(timeout),
            )
            self._owns_client = True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying client if we own it."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "HttpWorkflowService":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, workflow_id: str) -> Optional[WorkflowDetail]:
        response = self._request("GET", f"/workflows/{workflow_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return _detail_from_json(response.json())

    def get_by_ticket(self, ticket_key: str) -> List[WorkflowSummary]:
        response = self._request("GET", "/workflows", params={"ticket_key": ticket_key})
        response.raise_for_status()
        return [_summary_from_json(row) for row in response.json()]

    def get_latest_retryable_by_ticket(self, ticket_key: str) -> Optional[WorkflowSummary]:
        # The server has no dedicated endpoint; filter client-side.  The list
        # endpoint already returns newest-first.
        for summary in self.get_by_ticket(ticket_key):
            if summary.status.is_retryable():
                return summary
        return None

    def list(
        self,
        ticket_key: Optional[str] = None,
        status: Optional[WorkflowStatus] = None,
        limit: int = 50,
    ) -> List[WorkflowSummary]:
        params: Dict[str, Any] = {"limit": limit}
        if ticket_key is not None:
            params["ticket_key"] = ticket_key
        if status is not None:
            params["status"] = status.value
        response = self._request("GET", "/workflows", params=params)
        response.raise_for_status()
        return [_summary_from_json(row) for row in response.json()]

    def get_history(self, workflow_id: str) -> List[WorkflowHistoryEntry]:
        response = self._request("GET", f"/workflows/{workflow_id}/history")
        if response.status_code == 404:
            raise ValueError(f"Workflow not found: {workflow_id}")
        response.raise_for_status()
        return [_history_from_json(row) for row in response.json()]

    def get_audit_log(self, workflow_id: str) -> List[WorkflowAuditEntry]:
        response = self._request("GET", f"/workflows/{workflow_id}/audit-log")
        if response.status_code == 404:
            raise ValueError(f"Workflow not found: {workflow_id}")
        response.raise_for_status()
        return [_audit_from_json(workflow_id, row) for row in response.json()]

    def read_logs(
        self,
        workflow_id: str,
        stage: Optional[str] = None,
        after_offset: int = 0,
    ) -> List[WorkflowLogChunk]:
        """Drain the SSE log stream once and return the chunks present now.

        Uses the same SSE endpoint that ``stream_workflow_logs`` consumes; we
        rely on the server emitting all currently-available chunks in the
        first poll cycle, then either a ``stream_end`` event (terminal
        workflow) or a poll-interval pause.  A short read timeout converts
        the latter into a clean disconnect.
        """
        params: Dict[str, Any] = {"after_offset": after_offset}
        if stage is not None:
            params["stage"] = stage

        chunks: List[WorkflowLogChunk] = []
        # Per-stage accumulator so multiple chunk events for the same stage
        # collapse into one WorkflowLogChunk matching the Local implementation.
        per_stage: Dict[str, Dict[str, Any]] = {}

        try:
            with self._client.stream(
                "GET",
                self._url(f"/workflows/{workflow_id}/logs"),
                params=params,
                headers=self._headers(accept="text/event-stream"),
                timeout=httpx.Timeout(
                    connect=self._timeout,
                    read=LOG_SNAPSHOT_READ_TIMEOUT_S,
                    write=self._timeout,
                    pool=self._timeout,
                ),
            ) as response:
                if response.status_code == 404:
                    raise ValueError(f"Workflow not found: {workflow_id}")
                response.raise_for_status()
                for event in _iter_sse_events(response):
                    payload = event.get("data")
                    if payload is None:
                        continue
                    if payload.get("kind") == "stream_end":
                        break
                    st = payload.get("stage")
                    content = payload.get("content")
                    offset = payload.get("offset", 0)
                    if not st or content is None:
                        continue
                    bucket = per_stage.setdefault(
                        st,
                        {"offset": offset, "content": "", "path": f"<remote:{st}>"},
                    )
                    # Keep the earliest offset; concatenate subsequent
                    # content (poll-by-poll the server emits only new bytes).
                    bucket["content"] += content
        except httpx.ReadTimeout:
            # Expected when the workflow is still active: the initial burst
            # of chunks has arrived and we should not block on the next poll.
            pass

        for st, bucket in per_stage.items():
            chunks.append(
                WorkflowLogChunk(
                    workflow_id=workflow_id,
                    stage=st,
                    path=str(bucket["path"]),
                    content=bucket["content"],
                    offset=int(bucket["offset"]),
                )
            )
        return chunks

    def stream_events(
        self,
        workflow_id: str,
        after_seq: int = 0,
    ) -> Iterable[WorkflowEvent]:
        """Yield events from the SSE stream, reconnecting on transport errors.

        Stops when the server emits a ``stream_end`` event (workflow reached
        a terminal state) or when reconnect attempts are exhausted.
        """
        return _SseEventIterator(
            client=self._client,
            url=self._url(f"/workflows/{workflow_id}/events"),
            workflow_id=workflow_id,
            after_seq=max(0, after_seq),
            headers=self._headers(accept="text/event-stream"),
            stream_timeout=self._timeout,
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
        body: Dict[str, Any] = {"actor": actor}
        if reason is not None:
            body["reason"] = reason
        response = self._request(
            "POST",
            f"/workflows/{workflow_id}/cancel",
            json=body,
        )
        if response.status_code == 404:
            raise ValueError(f"Workflow not found: {workflow_id}")
        if response.status_code == 409:
            raise ValueError(
                response.json().get("detail") or f"Workflow {workflow_id} is already terminal"
            )
        response.raise_for_status()

    def mark_interrupted(
        self,
        workflow_id: str,
        failed_node: Optional[str] = None,
        actor: str = "system",
    ) -> None:
        body: Dict[str, Any] = {"actor": actor}
        if failed_node is not None:
            body["failed_node"] = failed_node
        response = self._request(
            "POST",
            f"/admin/workflows/{workflow_id}/mark-interrupted",
            json=body,
        )
        if response.status_code == 404:
            raise ValueError(f"Workflow not found: {workflow_id}")
        response.raise_for_status()

    def mark_failed(
        self,
        workflow_id: str,
        reason: str,
        actor: str = "system",
    ) -> None:
        # ``mark_failed`` is a server-internal hook used by the background
        # dispatcher when a graph drive raises uncaught.  No client-facing
        # endpoint exists; callers should not invoke this from the HTTP
        # transport.
        raise RemoteOperationNotSupported(
            "mark_failed has no client-facing endpoint; it is a server-side "
            "hook for the background dispatcher only."
        )

    def clear_db(self) -> tuple[int, int]:
        response = self._request("POST", "/admin/clear-db")
        response.raise_for_status()
        payload = response.json()
        return (int(payload["workflows"]), int(payload["checkpoints"]))

    # ------------------------------------------------------------------
    # Graph-running operations
    # ------------------------------------------------------------------

    def start(self, request: WorkflowStartRequest) -> WorkflowRunResult:
        body: Dict[str, Any] = {
            "ticket_key": request.ticket_key,
            "dry_run": request.dry_run,
        }
        if request.workflow_id is not None:
            body["workflow_id"] = request.workflow_id
        response = self._request("POST", "/workflows", json=body)
        response.raise_for_status()
        return _run_result_from_json(response.json())

    def approve_plan(self, workflow_id: str) -> WorkflowRunResult:
        return self._post_run(f"/workflows/{workflow_id}/approve-plan", workflow_id)

    def reject_plan(self, workflow_id: str, reason: Optional[str]) -> WorkflowRunResult:
        body: Dict[str, Any] = {}
        if reason is not None:
            body["reason"] = reason
        return self._post_run(f"/workflows/{workflow_id}/reject-plan", workflow_id, body=body)

    def submit_clarification(
        self,
        workflow_id: str,
        answers: List[Dict[str, str]],
    ) -> WorkflowRunResult:
        body: Dict[str, Any] = {"answers": [dict(a) for a in answers]}
        return self._post_run(f"/workflows/{workflow_id}/clarification", workflow_id, body=body)

    def retry(self, workflow_id: str) -> WorkflowRunResult:
        return self._post_run(f"/workflows/{workflow_id}/retry", workflow_id)

    def approve_pr(self, workflow_id: str) -> WorkflowRunResult:
        return self._post_run(f"/workflows/{workflow_id}/approve-pr", workflow_id)

    def comment_pr(self, workflow_id: str, comments: str) -> WorkflowRunResult:
        body: Dict[str, Any] = {"comments": comments}
        return self._post_run(f"/workflows/{workflow_id}/comment-pr", workflow_id, body=body)

    def reject_pr(self, workflow_id: str, reason: Optional[str]) -> WorkflowRunResult:
        body: Dict[str, Any] = {}
        if reason is not None:
            body["reason"] = reason
        return self._post_run(f"/workflows/{workflow_id}/reject-pr", workflow_id, body=body)

    def _post_run(
        self,
        path: str,
        workflow_id: str,
        *,
        body: Optional[Dict[str, Any]] = None,
    ) -> WorkflowRunResult:
        """Shared POST + error-mapping helper for graph-running endpoints.

        Maps 404 to ``ValueError("Workflow not found: ...")`` and 409 to
        ``ValueError(<server detail>)`` so callers see the same exceptions
        :class:`LocalWorkflowService` raises for invalid state transitions.
        """
        response = self._request("POST", path, json=body)
        if response.status_code == 404:
            raise ValueError(f"Workflow not found: {workflow_id}")
        if response.status_code == 409:
            detail = response.json().get("detail") or (
                f"Workflow {workflow_id} is in an incompatible state for this action"
            )
            raise ValueError(detail)
        response.raise_for_status()
        return _run_result_from_json(response.json())

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> httpx.Response:
        return self._client.request(
            method,
            self._url(path),
            params=params,
            json=json,
            headers=self._headers(),
        )

    def _url(self, path: str) -> str:
        # httpx.Client base_url support is a little finicky when the client
        # was passed in (e.g. test transports).  Build absolute URLs ourselves
        # so the same code path works for both production and tests.
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self._base_url}{path}"

    def _headers(self, *, accept: Optional[str] = None) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if accept is not None:
            headers["Accept"] = accept
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        _inject_otel_headers(headers)
        return headers


# ---------------------------------------------------------------------------
# SSE event iterator with reconnect
# ---------------------------------------------------------------------------


class _SseEventIterator:
    """Iterator over workflow events from a server SSE stream with reconnect."""

    def __init__(
        self,
        *,
        client: httpx.Client,
        url: str,
        workflow_id: str,
        after_seq: int,
        headers: Dict[str, str],
        stream_timeout: float,
    ) -> None:
        self._client = client
        self._url = url
        self._workflow_id = workflow_id
        self._last_seq = after_seq
        self._headers = headers
        self._stream_timeout = stream_timeout
        self._stream_ended = False

    def __iter__(self) -> Iterator[WorkflowEvent]:
        return self._run()

    def _run(self) -> Iterator[WorkflowEvent]:
        attempts = 0
        while not self._stream_ended:
            try:
                yielded_any = False
                for event in self._read_stream():
                    yielded_any = True
                    attempts = 0  # progress resets the budget
                    yield event
                # Stream ended cleanly (server closed the response) without
                # a stream_end event — treat as terminal so we don't busy-loop.
                if not self._stream_ended:
                    if not yielded_any:
                        return
                    self._stream_ended = True
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ReadTimeout) as exc:
                attempts += 1
                if attempts > MAX_RECONNECT_ATTEMPTS:
                    logger.warning(
                        "stream_events: giving up after %d reconnect attempts (%s)",
                        attempts,
                        exc,
                    )
                    return
                logger.info(
                    "stream_events: reconnecting after %s (attempt %d, last_seq=%d)",
                    exc.__class__.__name__,
                    attempts,
                    self._last_seq,
                )
                # ``RECONNECT_BACKOFF_S`` is a module attribute so tests can
                # zero it; we still call sleep so production gets the backoff.
                import time

                time.sleep(RECONNECT_BACKOFF_S)

    def _read_stream(self) -> Iterator[WorkflowEvent]:
        params: Dict[str, Any] = {"after_seq": self._last_seq}
        with self._client.stream(
            "GET",
            self._url,
            params=params,
            headers=self._headers,
            timeout=httpx.Timeout(
                connect=self._stream_timeout,
                read=None,  # block until data or transport error
                write=self._stream_timeout,
                pool=self._stream_timeout,
            ),
        ) as response:
            if response.status_code == 404:
                raise ValueError(f"Workflow not found: {self._workflow_id}")
            response.raise_for_status()
            for event in _iter_sse_events(response):
                payload = event.get("data")
                if payload is None:
                    continue
                kind = payload.get("kind")
                seq = int(payload.get("seq") or 0)
                if seq:
                    self._last_seq = seq
                if kind == "stream_end":
                    self._stream_ended = True
                    return
                yield WorkflowEvent(
                    workflow_id=self._workflow_id,
                    seq=seq,
                    kind=str(kind),
                    node=payload.get("node"),
                    data=payload.get("data"),
                )


# ---------------------------------------------------------------------------
# SSE wire parsing
# ---------------------------------------------------------------------------


def _iter_sse_events(response: httpx.Response) -> Iterator[Dict[str, Any]]:
    """Yield parsed SSE events from a streaming :class:`httpx.Response`.

    Each yielded dict contains the parsed JSON ``data`` payload (or ``None``
    when the event has no body) and the raw ``id`` field if present.
    Heartbeat comment lines (``: ping``) are skipped silently.
    """
    data_lines: List[str] = []
    event_id: Optional[str] = None
    for raw_line in response.iter_lines():
        # httpx may yield ``str`` or ``bytes`` depending on transport; normalise.
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        if line == "":
            if data_lines:
                payload_text = "\n".join(data_lines)
                try:
                    parsed: Optional[Dict[str, Any]] = json.loads(payload_text)
                except json.JSONDecodeError:
                    logger.debug("SSE: dropping non-JSON event payload: %r", payload_text)
                    parsed = None
                yield {"id": event_id, "data": parsed}
            data_lines = []
            event_id = None
            continue
        if line.startswith(":"):
            # Comment / heartbeat — ignore.
            continue
        if line.startswith("id:"):
            event_id = line[3:].lstrip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
            continue
        # Other SSE fields (event:, retry:) are unused by this server.

    if data_lines:
        payload_text = "\n".join(data_lines)
        try:
            parsed = json.loads(payload_text)
        except json.JSONDecodeError:
            parsed = None
        yield {"id": event_id, "data": parsed}


# ---------------------------------------------------------------------------
# OTel propagation helper
# ---------------------------------------------------------------------------


def _inject_otel_headers(headers: Dict[str, str]) -> None:
    """Attach the active W3C trace context to ``headers`` (best effort)."""
    try:
        from opentelemetry.propagate import inject as _otel_inject

        carrier: Dict[str, str] = {}
        _otel_inject(carrier)
        for key in ("traceparent", "tracestate"):
            value = carrier.get(key)
            if value:
                headers[key] = value
    except Exception:  # pragma: no cover - best effort
        return


# ---------------------------------------------------------------------------
# JSON -> DTO mappers
# ---------------------------------------------------------------------------


def _summary_from_json(row: Dict[str, Any]) -> WorkflowSummary:
    return WorkflowSummary(
        id=row["id"],
        ticket_key=row["ticket_key"],
        status=WorkflowStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        pr_url=row.get("pr_url"),
    )


def _detail_from_json(row: Dict[str, Any]) -> WorkflowDetail:
    return WorkflowDetail(
        id=row["id"],
        ticket_key=row["ticket_key"],
        status=WorkflowStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        pr_url=row.get("pr_url"),
        work_plan=row.get("work_plan"),
        code_generation_summary=row.get("code_generation_summary"),
        clarification_history=list(row.get("clarification_history") or []),
        pr_comments=row.get("pr_comments"),
        usage_summary=dict(row.get("usage_summary") or {}),
        retry_count=int(row.get("retry_count") or 0),
    )


def _run_result_from_json(row: Dict[str, Any]) -> WorkflowRunResult:
    final_status_raw = row.get("final_status")
    final_status = WorkflowStatus(final_status_raw) if final_status_raw else WorkflowStatus.PENDING
    return WorkflowRunResult(
        workflow_id=row["workflow_id"],
        ticket_key=row.get("ticket_key"),
        final_status=final_status,
        interrupted=bool(row.get("interrupted", False)),
        error=row.get("error"),
        code_generation_summary=row.get("code_generation_summary"),
        pr_url=row.get("pr_url"),
        failed_node=row.get("failed_node"),
        final_state=None,
    )


def _history_from_json(row: Dict[str, Any]) -> WorkflowHistoryEntry:
    return WorkflowHistoryEntry(
        step=int(row["step"]),
        node=row["node"],
        outcome=row["outcome"],
        result_keys=list(row.get("result_keys") or []),
        error=row.get("error"),
    )


def _audit_from_json(workflow_id: str, row: Dict[str, Any]) -> WorkflowAuditEntry:
    return WorkflowAuditEntry(
        workflow_id=row.get("workflow_id") or workflow_id,
        actor=row.get("actor", ""),
        action=row.get("action", ""),
        timestamp=row.get("timestamp", ""),
        details=row.get("details"),
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_http_workflow_service(
    base_url: str,
    *,
    token: Optional[str] = None,
    client: Optional[httpx.Client] = None,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> HttpWorkflowService:
    """Return an :class:`HttpWorkflowService` configured against ``base_url``.

    Pass ``client`` to inject a test transport (e.g.
    ``httpx.Client(transport=httpx.ASGITransport(app=fastapi_app))``).
    """
    return HttpWorkflowService(
        base_url=base_url,
        token=token,
        client=client,
        timeout=timeout,
    )


__all__ = [
    "HttpWorkflowService",
    "RemoteOperationNotSupported",
    "build_http_workflow_service",
    "LOG_SNAPSHOT_READ_TIMEOUT_S",
    "RECONNECT_BACKOFF_S",
    "MAX_RECONNECT_ATTEMPTS",
]
