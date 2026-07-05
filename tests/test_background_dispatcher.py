"""Unit tests for :mod:`orchestrator.server.background`."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

import pytest

from orchestrator.server.background import (
    BackgroundDispatcher,
    SyncBackgroundDispatcher,
)
from orchestrator.subprocess_registry import (
    SUBPROCESS_REGISTRY,
    get_current_workflow_id,
    set_current_workflow_id,
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


# ---------------------------------------------------------------------------
# Thread-local workflow id + cancel
# ---------------------------------------------------------------------------


def _spawn_tracked_sleep(seconds: int = 60) -> subprocess.Popen:
    """Spawn ``sleep`` in its own process group, mimicking utils.py Popens."""
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({seconds})"],
        start_new_session=True,
    )


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover - would only fire cross-user
        return True
    return True


@pytest.fixture(autouse=True)
def _clear_registry():
    """Ensure the process-wide singleton starts each test empty."""
    # Snapshot for defensive cleanup; tests should not leak.
    yield
    SUBPROCESS_REGISTRY.terminate_all(grace_s=1.0)


def test_worker_thread_sets_and_clears_current_workflow_id() -> None:
    dispatcher = BackgroundDispatcher(max_workers=1)
    observed: dict[str, object] = {}
    done = threading.Event()

    def task() -> None:
        observed["inside"] = get_current_workflow_id()
        done.set()

    try:
        assert dispatcher.submit("wf-tls", task) is True
        assert done.wait(timeout=2.0)
        assert observed["inside"] == "wf-tls"
    finally:
        dispatcher.shutdown(wait=True)
    # Calling thread's TLS was not clobbered by the worker.
    assert get_current_workflow_id() is None


def test_thread_local_workflow_id_isolation() -> None:
    dispatcher = BackgroundDispatcher(max_workers=2)
    seen: dict[str, object] = {}
    barrier = threading.Barrier(2)
    done = threading.Event()

    def task(name: str) -> None:
        # Ensure both workers are inside their TLS scope simultaneously.
        barrier.wait(timeout=2.0)
        seen[name] = get_current_workflow_id()
        if len(seen) == 2:
            done.set()

    try:
        assert dispatcher.submit("wf-a", task, "a") is True
        assert dispatcher.submit("wf-b", task, "b") is True
        assert done.wait(timeout=2.0)
        assert seen == {"a": "wf-a", "b": "wf-b"}
    finally:
        dispatcher.shutdown(wait=True)


def test_cancel_terminates_registered_subprocess() -> None:
    dispatcher = BackgroundDispatcher(max_workers=1)
    proc = _spawn_tracked_sleep(30)
    try:
        SUBPROCESS_REGISTRY.register("wf-c", proc)
        assert _pid_alive(proc.pid)
        dispatcher.cancel("wf-c")
        # SIGTERM to process group; grace is up to 5s but python sleep exits fast.
        assert proc.wait(timeout=5.0) != 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        dispatcher.shutdown(wait=True)


def test_cancel_terminates_process_group() -> None:
    """A parent that forks a child should have both killed via killpg."""
    dispatcher = BackgroundDispatcher(max_workers=1)
    # Parent starts a background python sleep and prints its pid, then waits.
    script = (
        "import os, subprocess, sys, time;"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
        "sys.stdout.write(str(p.pid) + '\\n'); sys.stdout.flush();"
        "p.wait()"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert proc.stdout is not None
        child_pid_line = proc.stdout.readline().strip()
        assert child_pid_line, "parent did not report child pid"
        child_pid = int(child_pid_line)
        assert _pid_alive(child_pid)
        SUBPROCESS_REGISTRY.register("wf-pg", proc)
        dispatcher.cancel("wf-pg")
        assert proc.wait(timeout=5.0) != 0
        # Give the OS a beat to reap the child.
        deadline = time.monotonic() + 3.0
        while _pid_alive(child_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _pid_alive(child_pid), f"child pid {child_pid} survived cancel"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        dispatcher.shutdown(wait=True)


def test_cancel_is_no_op_when_nothing_registered() -> None:
    dispatcher = BackgroundDispatcher(max_workers=1)
    try:
        # Must not raise.
        dispatcher.cancel("unknown-workflow")
    finally:
        dispatcher.shutdown(wait=True)


def test_multiple_subprocesses_per_workflow_all_killed() -> None:
    dispatcher = BackgroundDispatcher(max_workers=1)
    procs = [_spawn_tracked_sleep(30) for _ in range(3)]
    try:
        for proc in procs:
            SUBPROCESS_REGISTRY.register("wf-multi", proc)
        dispatcher.cancel("wf-multi")
        for proc in procs:
            assert proc.wait(timeout=5.0) != 0
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        dispatcher.shutdown(wait=True)


def test_shutdown_terminates_all_registered_subprocesses() -> None:
    dispatcher = BackgroundDispatcher(max_workers=1)
    proc_a = _spawn_tracked_sleep(30)
    proc_b = _spawn_tracked_sleep(30)
    SUBPROCESS_REGISTRY.register("wf-x", proc_a)
    SUBPROCESS_REGISTRY.register("wf-y", proc_b)
    try:
        dispatcher.shutdown(wait=True)
        assert proc_a.wait(timeout=5.0) != 0
        assert proc_b.wait(timeout=5.0) != 0
    finally:
        for proc in (proc_a, proc_b):
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def test_sync_dispatcher_cancel_is_no_op() -> None:
    dispatcher = SyncBackgroundDispatcher()
    # Even with a stray registration, SyncBackgroundDispatcher.cancel must
    # not touch subprocesses -- the inline test double owns no children.
    proc = _spawn_tracked_sleep(5)
    try:
        SUBPROCESS_REGISTRY.register("wf-sync", proc)
        dispatcher.cancel("wf-sync")
        assert _pid_alive(proc.pid), "SyncBackgroundDispatcher.cancel must not kill subprocesses"
    finally:
        SUBPROCESS_REGISTRY.terminate("wf-sync", grace_s=1.0)


def test_set_current_workflow_id_helper_round_trips() -> None:
    assert get_current_workflow_id() is None
    set_current_workflow_id("wf-manual")
    try:
        assert get_current_workflow_id() == "wf-manual"
    finally:
        set_current_workflow_id(None)
    assert get_current_workflow_id() is None
