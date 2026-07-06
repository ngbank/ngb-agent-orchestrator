"""Registry that maps workflow ids to their live child subprocesses.

The background dispatcher spawns two long-lived subprocesses for every
graph drive: a LiteLLM proxy (in ``goose_session``) and a Goose CLI
invocation (in ``run_and_tee``).  Cancelling or interrupting a workflow
must terminate those subprocesses; otherwise they keep making LLM calls
and holding open sockets long after the workflow row has been marked
``cancelled`` / ``failed``.

The registry provides:

- A thread-local *current workflow id* so ``utils.py`` can register a
  ``Popen`` without every call site needing an explicit ``workflow_id``
  parameter.  The dispatcher's worker thread sets the id for the duration
  of the graph drive.
- A thread-safe ``workflow_id -> list[Popen]`` map with ``register`` /
  ``unregister`` used by the subprocess call sites.
- ``terminate(wf_id)`` and ``terminate_all()`` which POSIX-``killpg``
  the process group with SIGTERM, wait for a short grace period, then
  SIGKILL any survivors.  The graceful stage matters because the LiteLLM
  proxy owns child worker processes that only clean up on SIGTERM.

Non-POSIX platforms are supported at the import level (Windows has no
``killpg``); ``terminate`` falls back to a best-effort ``Popen.terminate``
on those platforms.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_HAS_KILLPG = hasattr(os, "killpg") and hasattr(os, "getpgid")


class SubprocessRegistry:
    """Thread-safe workflow_id -> [Popen] map with group termination."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._procs: Dict[str, List[subprocess.Popen]] = {}

    def register(self, workflow_id: str, proc: subprocess.Popen) -> None:
        """Track ``proc`` under ``workflow_id``.

        Safe to call repeatedly.  No-op when ``workflow_id`` is falsy so
        subprocess call sites can invoke this unconditionally.
        """
        if not workflow_id:
            return
        with self._lock:
            self._procs.setdefault(workflow_id, []).append(proc)

    def unregister(self, workflow_id: str, proc: subprocess.Popen) -> None:
        """Stop tracking ``proc``.  Silent if not registered."""
        if not workflow_id:
            return
        with self._lock:
            bucket = self._procs.get(workflow_id)
            if bucket is None:
                return
            try:
                bucket.remove(proc)
            except ValueError:
                pass
            if not bucket:
                self._procs.pop(workflow_id, None)

    def has(self, workflow_id: str) -> bool:
        with self._lock:
            return bool(self._procs.get(workflow_id))

    def terminate(self, workflow_id: str, grace_s: float = 5.0) -> int:
        """Terminate every subprocess registered under ``workflow_id``.

        Sends SIGTERM to each subprocess's process group (POSIX), waits up
        to ``grace_s`` seconds for exit, then SIGKILLs any survivors.
        Returns the number of subprocesses that were signalled.
        """
        with self._lock:
            procs = self._procs.pop(workflow_id, [])
        return _terminate_procs(procs, grace_s=grace_s, context=f"workflow={workflow_id}")

    def terminate_all(self, grace_s: float = 5.0) -> int:
        """Terminate every registered subprocess across all workflows.

        Used by the FastAPI lifespan hook on server shutdown so that
        SIGTERM / SIGINT / ``docker stop`` do not orphan children.
        """
        with self._lock:
            all_procs: List[subprocess.Popen] = []
            for procs in self._procs.values():
                all_procs.extend(procs)
            self._procs.clear()
        return _terminate_procs(all_procs, grace_s=grace_s, context="shutdown")


def _terminate_procs(
    procs: List[subprocess.Popen],
    *,
    grace_s: float,
    context: str,
) -> int:
    if not procs:
        return 0
    for proc in procs:
        _signal_group(proc, signal.SIGTERM, context)
    for proc in procs:
        try:
            proc.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            logger.warning(
                "Subprocess pid=%s did not exit after SIGTERM within %.1fs (%s); sending SIGKILL",
                proc.pid,
                grace_s,
                context,
            )
            _signal_group(proc, signal.SIGKILL, context)
            try:
                proc.wait(timeout=grace_s)
            except subprocess.TimeoutExpired:
                logger.error(
                    "Subprocess pid=%s did not exit after SIGKILL within %.1fs (%s)",
                    proc.pid,
                    grace_s,
                    context,
                )
        except Exception:
            logger.exception("Error waiting for subprocess pid=%s (%s)", proc.pid, context)
    return len(procs)


def _signal_group(proc: subprocess.Popen, sig: signal.Signals, context: str) -> None:
    if proc.poll() is not None:
        return
    if _HAS_KILLPG:
        try:
            os.killpg(os.getpgid(proc.pid), sig)
            return
        except ProcessLookupError:
            return
        except OSError:
            logger.exception(
                "killpg failed for pid=%s sig=%s (%s); falling back to proc.send_signal",
                proc.pid,
                sig.name,
                context,
            )
    try:
        proc.send_signal(sig)
    except ProcessLookupError:
        return
    except Exception:
        logger.exception("send_signal failed for pid=%s sig=%s (%s)", proc.pid, sig.name, context)


# ---------------------------------------------------------------------------
# Thread-local current workflow id
# ---------------------------------------------------------------------------


_thread_local = threading.local()


def set_current_workflow_id(workflow_id: Optional[str]) -> None:
    """Bind ``workflow_id`` to the calling thread for subprocess lookup."""
    _thread_local.workflow_id = workflow_id


def get_current_workflow_id() -> Optional[str]:
    """Return the workflow id bound to the calling thread, if any."""
    return getattr(_thread_local, "workflow_id", None)


# Process-wide singleton.  Import as ``from orchestrator.subprocess_registry
# import SUBPROCESS_REGISTRY``.
SUBPROCESS_REGISTRY = SubprocessRegistry()


__all__ = [
    "SUBPROCESS_REGISTRY",
    "SubprocessRegistry",
    "get_current_workflow_id",
    "set_current_workflow_id",
]
