"""Background dispatcher for fire-and-forget graph-running endpoints.

The orchestrator server's graph-running routes (``start``, ``approve_plan``,
``comment_pr``, …) used to block the HTTP request until the langgraph +
Goose + LiteLLM run completed — easily several minutes.  That model is
incompatible with HTTP clients, load balancers, and Ctrl-C semantics.

:class:`BackgroundDispatcher` runs each graph drive on a worker thread,
tracks in-flight workflows so duplicate submissions can be rejected with
``409 Conflict``, and shuts down with the FastAPI app.

The dispatcher owns no HTTP / FastAPI types — routes hand it bound
``WorkflowService`` methods and an optional ``on_failure`` callback used to
update workflow status when an uncaught exception leaks out of the graph
drive.  Tests inject a :class:`SyncBackgroundDispatcher` to keep route
assertions deterministic.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional

from orchestrator.logging_setup import attach_workflow_file_handler, detach_workflow_file_handler

logger = logging.getLogger(__name__)

# Default worker count for the background dispatcher.  Each graph drive
# spawns its own LiteLLM proxy + Goose subprocess so the cap is mostly a
# safety valve against runaway submissions; production deployments tune
# via ``ORCHESTRATOR_BACKGROUND_WORKERS``.
DEFAULT_MAX_WORKERS: int = 4


# ---------------------------------------------------------------------------
# Public protocol
# ---------------------------------------------------------------------------


class BackgroundDispatcherProtocol:
    """Surface used by route handlers.

    Declared as a plain class (not ``typing.Protocol``) so production and
    test implementations can subclass it for clearer introspection.
    """

    def submit(
        self,
        workflow_id: str,
        fn: Callable[..., Any],
        *args: Any,
        on_failure: Optional[Callable[[BaseException], None]] = None,
        **kwargs: Any,
    ) -> bool:  # pragma: no cover - abstract
        raise NotImplementedError

    def is_in_flight(self, workflow_id: str) -> bool:  # pragma: no cover - abstract
        raise NotImplementedError

    def shutdown(self, *, wait: bool = False) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Production implementation
# ---------------------------------------------------------------------------


class BackgroundDispatcher(BackgroundDispatcherProtocol):
    """Thread-pool-backed dispatcher used by the production FastAPI app."""

    def __init__(self, max_workers: int = DEFAULT_MAX_WORKERS) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="wf-bg")
        self._in_flight: Dict[str, Future[Any]] = {}
        self._lock = threading.Lock()
        self._shutdown = False

    def submit(
        self,
        workflow_id: str,
        fn: Callable[..., Any],
        *args: Any,
        on_failure: Optional[Callable[[BaseException], None]] = None,
        **kwargs: Any,
    ) -> bool:
        """Submit ``fn(*args, **kwargs)`` to run on a worker thread.

        Returns ``True`` when the task was accepted, ``False`` when another
        task is already running for ``workflow_id`` (caller should respond
        with HTTP 409).  Raises ``RuntimeError`` if called after
        :meth:`shutdown`.
        """
        with self._lock:
            if self._shutdown:
                raise RuntimeError("BackgroundDispatcher is shut down")
            existing = self._in_flight.get(workflow_id)
            if existing is not None and not existing.done():
                return False
            future = self._executor.submit(self._run, workflow_id, fn, args, kwargs, on_failure)
            self._in_flight[workflow_id] = future

        def _on_done(f: "Future[Any]", wid: str = workflow_id) -> None:
            self._cleanup(wid, f)

        future.add_done_callback(_on_done)
        return True

    def is_in_flight(self, workflow_id: str) -> bool:
        with self._lock:
            future = self._in_flight.get(workflow_id)
            return future is not None and not future.done()

    def shutdown(self, *, wait: bool = False) -> None:
        """Refuse new submissions and tear down the executor.

        ``wait=False`` is the production default — graphs may run for
        minutes and blocking shutdown would stall systemd / uvicorn.  The
        executor's worker threads keep running until they finish on their
        own; long-running graph drives are not cancellable by design (the
        Goose subprocess + LiteLLM proxy + langgraph stream do not respect
        thread interruption).
        """
        with self._lock:
            self._shutdown = True
        self._executor.shutdown(wait=wait, cancel_futures=True)

    # -- internals ------------------------------------------------------

    def _run(
        self,
        workflow_id: str,
        fn: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: Dict[str, Any],
        on_failure: Optional[Callable[[BaseException], None]],
    ) -> Any:
        handler = attach_workflow_file_handler(workflow_id)
        try:
            return fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - we re-raise after notifying
            logger.exception("Background graph drive for workflow %s raised", workflow_id)
            if on_failure is not None:
                try:
                    on_failure(exc)
                except Exception:
                    logger.exception("on_failure handler for workflow %s raised", workflow_id)
            raise
        finally:
            detach_workflow_file_handler(handler)

    def _cleanup(self, workflow_id: str, future: Future[Any]) -> None:
        with self._lock:
            current = self._in_flight.get(workflow_id)
            if current is future:
                del self._in_flight[workflow_id]


# ---------------------------------------------------------------------------
# Test implementation
# ---------------------------------------------------------------------------


class SyncBackgroundDispatcher(BackgroundDispatcherProtocol):
    """Runs submitted callables inline on the calling thread.

    Used by tests so route assertions can read the workflow's final state
    immediately after the POST returns.  The duplicate-submission guard
    behaves the same as production (re-entrant calls during the inline
    execution return ``False``).
    """

    def __init__(self) -> None:
        self._running: set[str] = set()
        self.calls: list[tuple[str, Callable[..., Any], tuple[Any, ...], Dict[str, Any]]] = []

    def submit(
        self,
        workflow_id: str,
        fn: Callable[..., Any],
        *args: Any,
        on_failure: Optional[Callable[[BaseException], None]] = None,
        **kwargs: Any,
    ) -> bool:
        if workflow_id in self._running:
            return False
        self._running.add(workflow_id)
        self.calls.append((workflow_id, fn, args, dict(kwargs)))
        try:
            try:
                fn(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001
                if on_failure is not None:
                    try:
                        on_failure(exc)
                    except Exception:
                        logger.exception("on_failure handler for workflow %s raised", workflow_id)
                    # An on_failure handler is the contract for visibility:
                    # the production dispatcher swallows worker exceptions
                    # because the HTTP route has already returned 202.  Match
                    # that behaviour here so route tests can assert on the
                    # post-failure state without 500s leaking out.
                else:
                    raise
        finally:
            self._running.discard(workflow_id)
        return True

    def is_in_flight(self, workflow_id: str) -> bool:
        return workflow_id in self._running

    def shutdown(self, *, wait: bool = False) -> None:
        # Nothing to tear down — execution is inline.
        return None


__all__ = [
    "BackgroundDispatcher",
    "BackgroundDispatcherProtocol",
    "DEFAULT_MAX_WORKERS",
    "SyncBackgroundDispatcher",
]
