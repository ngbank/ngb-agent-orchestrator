"""Canonical helpers for writing, clearing, and reading node-failure state.

The graph carries two failure fields on ``OrchestratorState`` and its
per-stage TypedDicts:

* ``state.error`` — a human-readable message.
* ``state.failed_node`` — the node name that produced the failure, used by
  ``orchestrator.retry`` to compute the rewind point.

Before this module existed, individual nodes wrote the two fields as
literal dict keys and edges read them independently. Two concrete drift
risks followed:

1. **Edge asymmetry.** ``_route_after_work_planner`` checked ``error``
   while ``_route_after_generate_code`` checked ``failed_node``. A node
   that set only one field routed inconsistently depending on which
   parent edge fired.
2. **Write-site drift.** Nodes could set one field and forget the other.
   Retry rewinds only fire when ``failed_node`` is populated, but the
   error-routing edge only trips when ``error`` is populated.

This module concentrates the shape in one place. All new node code that
signals a failure should call :func:`mark_failure`; all edges that route
on failure should call :func:`has_failure`; retry's clear-on-rewind
should call :func:`clear_failure`.

The wire format is intentionally unchanged — ``WorkflowRunResult`` and
the REST schemas continue to expose ``error`` / ``failed_node`` as flat
fields. This module is an internal invariant guard, not a schema bump.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple

__all__ = [
    "mark_failure",
    "clear_failure",
    "has_failure",
    "get_failure",
    "assert_failure_consistent",
]


def mark_failure(node: str, error: str) -> Any:
    """Return the canonical partial-state update for a node failure.

    Nodes call ``return mark_failure(node, msg)`` (or
    ``return {**mark_failure(node, msg), ...other_updates}`` when they
    also need to write other keys) so both fields are always populated
    together.

    The return type is declared as ``Any`` so callers can return the
    result directly from a per-node TypedDict-annotated function
    without a redundant ``cast``. The runtime shape
    (``{"error": str, "failed_node": str}``) is enforced by the
    invariant tests in ``tests/test_failure.py`` and
    ``tests/test_failure_write_sites.py``.

    Args:
        node: The name of the failing node. Must be non-empty — an empty
            value would make the rewind lookup ambiguous.
        error: A human-readable error message. Must be non-empty — an
            empty message would make the error routing edge trip without
            any diagnostic detail.

    Raises:
        ValueError: when either ``node`` or ``error`` is empty.
    """
    if not node:
        raise ValueError("mark_failure requires a non-empty node name")
    if not error:
        raise ValueError("mark_failure requires a non-empty error message")
    return {"error": error, "failed_node": node}


def clear_failure() -> Any:
    """Return the canonical partial-state update that clears both fields.

    Used by :func:`orchestrator.retry.prepare_retry` when rewinding to
    the checkpoint before the failed node — so the re-run starts with a
    clean slate rather than the previous run's error still visible to
    edges and instrumentation.

    The return type is declared as ``Any`` for the same reason as
    :func:`mark_failure`: callers embed it via ``**clear_failure()`` or
    return it directly from per-node TypedDict-annotated functions.
    """
    return {"error": None, "failed_node": None}


def has_failure(state: Mapping[str, Any]) -> bool:
    """Return True when the state carries a failure signal.

    Returns True if EITHER ``error`` or ``failed_node`` is truthy. This
    is deliberate: the invariant on write is that both are populated
    together, but on read we tolerate legacy or partial state so that a
    drift bug fails safe (routes to error handling) rather than silently
    ignoring the failure and continuing down the happy path.

    Edge routers should call this instead of reading either field
    directly.
    """
    return bool(state.get("error")) or bool(state.get("failed_node"))


def get_failure(state: Mapping[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(error, failed_node)`` for structured consumers.

    Used by OTel instrumentation and DTO mapping to project the failure
    signal into their own shapes without touching the raw dict keys.
    Returns ``(None, None)`` when no failure is set.
    """
    return state.get("error"), state.get("failed_node")


def assert_failure_consistent(state: Mapping[str, Any]) -> None:
    """Raise ``AssertionError`` when ``error`` is set without ``failed_node``.

    Dev-time invariant helper. Test suites use this to enforce that
    every node that produces a user-facing error message also names the
    failing node so :mod:`orchestrator.retry` can compute a rewind
    point.

    The check is deliberately one-way rather than a strict XOR:

    * ``error`` set, ``failed_node`` unset → **rejected**. This was the
      actual drift bug — retry could not find where to rewind, and the
      user saw a message with nowhere to go.
    * ``failed_node`` set, ``error`` unset → **allowed**. The
      code_generator subgraph carries its error text in
      ``code_generation_summary.error`` / ``exec_error`` and only
      surfaces ``failed_node`` on the parent state; a strict XOR would
      false-positive that legitimate pattern.
    * Both set (proper failure via :func:`mark_failure`) → allowed.
    * Neither set (success path) → allowed.
    """
    error = state.get("error")
    failed_node = state.get("failed_node")
    if error and not failed_node:
        raise AssertionError(
            "Inconsistent failure state: "
            f"error={error!r} is set but failed_node is empty. "
            "Use orchestrator.failure.mark_failure to set both together."
        )
