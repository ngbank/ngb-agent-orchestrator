"""State contract for the shared repo_setup subgraph."""

from typing import Optional

from typing_extensions import TypedDict


class RepoSetupState(TypedDict, total=False):
    """Minimal state contract for the shared repo setup subgraph."""

    ticket_key: str
    workflow_id: Optional[str]
    repo_url: Optional[str]
    github_token: Optional[str]
    working_dir: Optional[str]
    error: Optional[str]
    exec_error: Optional[str]
    failed_node: Optional[str]
    execution_summary: Optional[dict]
