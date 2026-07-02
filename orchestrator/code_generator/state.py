"""
CodeGeneratorState with per-node focused TypedDicts.

Following the Interface Segregation Principle, each node within the code generator
has a focused TypedDict that declares only the keys it reads and writes.
CodeGeneratorState is the composition of all these TypedDicts for the subgraph.
"""

from typing import Optional

from typing_extensions import TypedDict


class PrepareWorkspaceInputState(TypedDict, total=False):
    """Input required by prepare_workspace node."""

    workflow_id: str
    ticket_key: str
    work_plan_data: dict


class PrepareWorkspaceOutputState(TypedDict, total=False):
    """Output produced by prepare_workspace node."""

    work_plan_path: str
    summary_path: str
    reasoning_path: str


class RunGooseInputState(TypedDict, total=False):
    """Input required by run_goose node."""

    workflow_id: str
    ticket_key: str
    working_dir: str
    work_plan_path: str
    summary_path: str
    reasoning_path: str
    code_generation_summary: Optional[dict]
    pr_comments: Optional[str]
    branch_prefix: str


class ProcessResultsInputState(TypedDict, total=False):
    """Input required by process_results node."""

    ticket_key: str
    summary_path: str


class ProcessResultsOutputState(TypedDict, total=False):
    """Output produced by process_results node."""

    code_generation_summary: Optional[dict]


class PersistResultsInputState(TypedDict, total=False):
    """Input required by persist_results node."""

    workflow_id: str
    code_generation_summary: Optional[dict]


class PersistResultsOutputState(TypedDict, total=False):
    """Output produced by persist_results node."""

    pr_url: Optional[str]
    failed_node: Optional[str]


class PushAndCreatePrInputState(TypedDict, total=False):
    """Input required by push_and_create_pr node."""

    workflow_id: str
    ticket_key: str
    working_dir: str
    repo_url: str
    github_token: str
    code_generation_summary: Optional[dict]
    work_plan_data: dict
    pr_comments: Optional[str]
    exec_error: Optional[str]


class PushAndCreatePrOutputState(TypedDict, total=False):
    """Output produced by push_and_create_pr node."""

    code_generation_summary: Optional[dict]
    failed_node: Optional[str]


class CleanupInputState(TypedDict, total=False):
    """Input required by cleanup node."""

    work_plan_path: str
    summary_path: str
    reasoning_path: str
    working_dir: str


class CodeGeneratorState(TypedDict, total=False):
    """
    CodeGeneratorState: state for the code_generator subgraph.

    Composition of all per-node TypedDicts for the subgraph. Each node is
    annotated with its narrower input/output types for IDE and mypy visibility,
    even though the runtime type remains CodeGeneratorState.

    CodeGeneratorState carries fields shared with OrchestratorState (which flow in from
    the parent graph and whose updates propagate back) plus execution-local fields
    that are only meaningful within the subgraph and are dropped on exit.
    """

    # --- shared with OrchestratorState ---
    workflow_id: str
    ticket_key: str
    work_plan_data: dict
    code_generation_summary: Optional[dict]
    failed_node: Optional[str]
    pr_url: Optional[str]
    pr_comments: Optional[str]

    # --- shared with OrchestratorState (routing) ---
    pr_approval_decision: Optional[str]  # "approved" | "rejected" | "commented"

    # --- subgraph-internal routing signal ---
    # Set by repo_setup on failure so edges can skip straight to
    # persist_results without running the Goose step.
    exec_error: Optional[str]

    # --- GitHub App token (ephemeral, subgraph-local) ---
    github_token: str

    # --- execution-local workspace fields ---
    repo_url: str
    working_dir: str
    work_plan_path: str
    summary_path: str
    reasoning_path: str
    branch_prefix: str
