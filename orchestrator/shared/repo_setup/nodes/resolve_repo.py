"""resolve_repo node for the shared repo_setup subgraph."""

import click

from orchestrator.shared.repo_setup.nodes.common import failure_update
from orchestrator.shared.repo_setup.primitives import resolve_repository_url
from orchestrator.shared.repo_setup.state import RepoSetupState


def build_resolve_repo_node(mode: str):
    """Build resolve_repo node callable with mode-specific error mapping."""

    def _node(state: RepoSetupState) -> dict:
        ticket_key = state.get("ticket_key", "")
        existing_repo_url = (state.get("repo_url") or "").strip()

        try:
            repo_url = resolve_repository_url(ticket_key, existing_repo_url)
            return {"repo_url": repo_url}
        except ValueError as exc:
            error_msg = str(exc)
            click.echo(f"❌ {error_msg}", err=True)
            return failure_update(state, error_msg, mode)

    return _node
