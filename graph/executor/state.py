"""
ExecutionState: state for the executor subgraph.

ExecutionState carries fields shared with OrchestratorState (which flow in from
the parent graph and whose updates propagate back) plus execution-local fields
that are only meaningful within the subgraph and are dropped on exit.
"""

from typing import Optional

from typing_extensions import TypedDict


class ExecutionState(TypedDict, total=False):
    # --- shared with OrchestratorState ---
    workflow_id: Optional[str]
    ticket_key: str
    work_plan_data: Optional[dict]
    execution_summary: Optional[dict]
    failed_node: Optional[str]
    pr_url: Optional[str]
    pr_comments: Optional[str]

    # --- subgraph-internal routing signal ---
    # Set by resolve_repo or clone_repo on failure so that edges can skip
    # straight to persist_results without running the Goose step.
    exec_error: Optional[str]

    # --- execution-local workspace fields ---
    repo_url: str
    working_dir: str
    work_plan_path: str
    summary_path: str
    reasoning_path: str
    exec_log_path: str
