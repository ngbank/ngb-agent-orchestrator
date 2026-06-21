"""WorkflowService abstraction — single boundary for every workflow operation.

Public surface:

* ``WorkflowService`` — the Protocol all callers depend on.
* ``LocalWorkflowService`` — in-process implementation wrapping the existing
  graph builder, SQLite repository, and log file helpers.
* ``build_local_workflow_service`` — factory that returns a ``LocalWorkflowService``
  wired with the default singletons.
* DTO types exchanged across the boundary (frozen dataclasses; transport-agnostic).

This module exists so that the dispatcher CLI and TUI can talk to a single
workflow contract.  A future HTTP-backed implementation will satisfy the same
``WorkflowService`` Protocol, letting the dispatcher target either a local
in-process orchestrator or a remote one without call-site changes.
"""

from .dtos import (
    AuditEntry,
    HistoryEntry,
    LogChunk,
    StartRequest,
    WorkflowDetail,
    WorkflowEvent,
    WorkflowRunResult,
    WorkflowSummary,
)
from .local import LocalWorkflowService, build_local_workflow_service
from .protocols import WorkflowService

__all__ = [
    "WorkflowService",
    "LocalWorkflowService",
    "build_local_workflow_service",
    "AuditEntry",
    "HistoryEntry",
    "LogChunk",
    "StartRequest",
    "WorkflowDetail",
    "WorkflowEvent",
    "WorkflowRunResult",
    "WorkflowSummary",
]
