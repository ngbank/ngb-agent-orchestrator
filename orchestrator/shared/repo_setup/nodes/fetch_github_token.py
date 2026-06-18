"""fetch_github_token node for the shared repo_setup subgraph."""

import click

from orchestrator.shared.repo_setup.nodes.common import failure_update
from orchestrator.shared.repo_setup.primitives import fetch_token_for_repo
from orchestrator.shared.repo_setup.state import RepoSetupState


def build_fetch_github_token_node(mode: str):
    """Build fetch_github_token node callable with mode-specific error mapping."""

    def _node(state: RepoSetupState) -> dict:
        ticket_key = state.get("ticket_key", "")
        repo_url = (state.get("repo_url") or "").strip()

        if not repo_url:
            error_msg = "Repository URL is required before fetching GitHub token"
            click.echo(f"❌ {error_msg}", err=True)
            return failure_update(state, error_msg, mode)

        if repo_url.startswith("git@"):
            click.echo("🔑 Skipping GitHub token fetch for SSH repository URL")
            return {}

        try:
            token = fetch_token_for_repo(ticket_key, repo_url)
            click.echo("✓ Fetched GitHub App installation token")
            return {"github_token": token} if token else {}
        except Exception as exc:  # noqa: BLE001
            error_msg = str(exc)
            click.echo(f"❌ {error_msg}", err=True)
            return failure_update(state, error_msg, mode)

    return _node
