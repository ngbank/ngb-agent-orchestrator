"""Unit tests for :mod:`orchestrator.server.background`."""

from __future__ import annotations

import threading
import time

import pytest

from orchestrator.server.background import (
    BackgroundDispatcher,
    SyncBackgroundDispatcher,
)

# ---------------------------------------------------------------------------
# BackgroundDispatcher (production thread-pool implementation)
# ---------------------------------------------------------------------------


def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_submit_runs_fn_on_worker_thread() -> None:
    dispatcher = BackgroundDispatcher(max_workers=2)
    try:
        ran = threading.Event()
        observed: dict[str, str] = {}

        def task() -> None:
            observed["thread"] = threading.current_thread().name
            ran.set()

        accepted = dispatcher.submit("wf-1", task)
        assert accepted is True
        assert ran.wait(timeout=2.0)
        assert observed["thread"].startswith("wf-bg")
    finally:
        dispatcher.shutdown(wait=True)


def test_duplicate_submission_for_same_workflow_returns_false() -> None:
    dispatcher = BackgroundDispatcher(max_workers=2)
    try:
        release = threading.Event()
        started = threading.Event()

        def blocker() -> None:
            started.set()
            release.wait(timeout=2.0)

        assert dispatcher.submit("wf-2", blocker) is True
        assert started.wait(timeout=2.0)
        # Second submission while the first is still running must be rejected.
        assert dispatcher.submit("wf-2", lambda: None) is False
        release.set()
        assert _wait_until(lambda: not dispatcher.is_in_flight("wf-2"))
    finally:
        dispatcher.shutdown(wait=True)


def test_submission_after_previous_completes_is_accepted() -> None:
    dispatcher = BackgroundDispatcher(max_workers=2)
    try:
        first_done = threading.Event()
        dispatcher.submit("wf-3", lambda: first_done.set())
        assert first_done.wait(timeout=2.0)
        assert _wait_until(lambda: not dispatcher.is_in_flight("wf-3"))

        second_done = threading.Event()
        assert dispatcher.submit("wf-3", lambda: second_done.set()) is True
        assert second_done.wait(timeout=2.0)
    finally:
        dispatcher.shutdown(wait=True)


def test_distinct_workflows_run_concurrently() -> None:
    dispatcher = BackgroundDispatcher(max_workers=2)
    try:
        release = threading.Event()
        started_a = threading.Event()
        started_b = threading.Event()

        def task_a() -> None:
            started_a.set()
            release.wait(timeout=2.0)

        def task_b() -> None:
            started_b.set()
            release.wait(timeout=2.0)

        dispatcher.submit("wf-a", task_a)
        dispatcher.submit("wf-b", task_b)

        assert started_a.wait(timeout=2.0)
        assert started_b.wait(timeout=2.0)
        release.set()
    finally:
        dispatcher.shutdown(wait=True)


def test_uncaught_exception_invokes_on_failure_and_cleans_up() -> None:
    dispatcher = BackgroundDispatcher(max_workers=2)
    try:
        captured: list[BaseException] = []

        def boom() -> None:
            raise RuntimeError("kaboom")

        dispatcher.submit(
            "wf-x",
            boom,
            on_failure=lambda exc: captured.append(exc),
        )
        assert _wait_until(lambda: not dispatcher.is_in_flight("wf-x"))
        assert len(captured) == 1
        assert isinstance(captured[0], RuntimeError)
    finally:
        dispatcher.shutdown(wait=True)


def test_on_failure_handler_exceptions_are_swallowed() -> None:
    dispatcher = BackgroundDispatcher(max_workers=2)
    try:

        def boom() -> None:
            raise RuntimeError("primary")

        def bad_handler(exc: BaseException) -> None:
            raise ValueError("secondary")

        # Should not crash the worker thread or the test process.
        dispatcher.submit("wf-y", boom, on_failure=bad_handler)
        assert _wait_until(lambda: not dispatcher.is_in_flight("wf-y"))
    finally:
        dispatcher.shutdown(wait=True)


def test_submit_after_shutdown_raises() -> None:
    dispatcher = BackgroundDispatcher(max_workers=1)
    dispatcher.shutdown(wait=True)
    with pytest.raises(RuntimeError):
        dispatcher.submit("wf-z", lambda: None)


# ---------------------------------------------------------------------------
# SyncBackgroundDispatcher (test double)
# ---------------------------------------------------------------------------


def test_sync_dispatcher_runs_inline_and_records_calls() -> None:
    dispatcher = SyncBackgroundDispatcher()
    counter = {"n": 0}

    def task(value: int) -> None:
        counter["n"] = value

    assert dispatcher.submit("wf-1", task, 42) is True
    assert counter["n"] == 42
    assert dispatcher.is_in_flight("wf-1") is False
    assert len(dispatcher.calls) == 1
    wid, fn, args, kwargs = dispatcher.calls[0]
    assert wid == "wf-1"
    assert fn is task
    assert args == (42,)
    assert kwargs == {}


def test_sync_dispatcher_swallows_when_on_failure_set() -> None:
    """Production dispatcher swallows worker errors (route already 202'd);
    sync impl mirrors that when an on_failure handler is provided."""
    dispatcher = SyncBackgroundDispatcher()
    captured: list[BaseException] = []

    def boom() -> None:
        raise RuntimeError("nope")

    # Should not raise — on_failure is the visibility contract.
    assert dispatcher.submit("wf-1", boom, on_failure=captured.append) is True
    assert len(captured) == 1
    assert isinstance(captured[0], RuntimeError)
    assert dispatcher.is_in_flight("wf-1") is False


def test_sync_dispatcher_propagates_when_no_on_failure() -> None:
    """Without an on_failure handler the inline impl re-raises so test
    misconfiguration is visible."""
    dispatcher = SyncBackgroundDispatcher()

    def boom() -> None:
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError):
        dispatcher.submit("wf-1", boom)
    assert dispatcher.is_in_flight("wf-1") is False


def test_sync_dispatcher_rejects_re_entrant_submission() -> None:
    dispatcher = SyncBackgroundDispatcher()
    observed: list[bool] = []

    def outer() -> None:
        observed.append(dispatcher.submit("wf-1", lambda: None))

    assert dispatcher.submit("wf-1", outer) is True
    assert observed == [False]
