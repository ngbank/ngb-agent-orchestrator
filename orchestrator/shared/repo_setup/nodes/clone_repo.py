"""clone_repo node for the shared repo_setup subgraph."""

import logging

import click

from orchestrator.shared.repo_setup.nodes.common import failure_update
from orchestrator.shared.repo_setup.repo_operations import clone_repository
from orchestrator.shared.repo_setup.state import RepoSetupState

logger = logging.getLogger(__name__)


def build_clone_repo_node(mode: str):
    """Build clone_repo node callable with mode-specific error mapping."""

    def _node(state: RepoSetupState) -> dict:
        workflow_id = state.get("workflow_id") or "unknown"
        repo_url = (state.get("repo_url") or "").strip()
        github_token = (state.get("github_token") or "").strip()

        if not repo_url:
            error_msg = "Repository URL is required before cloning"
            click.echo(f"❌ {error_msg}", err=True)
            return failure_update(state, error_msg, mode)

        prefix = f"ngb-{'generate_code' if mode == 'code_generator' else 'plan'}-{workflow_id}-"

        try:
            logger.info("=== git clone %s ===", repo_url)
            working_dir = clone_repository(
                repo_url,
                github_token,
                temp_prefix=prefix,
            )
            click.echo(f"📂 Cloning {repo_url} into {working_dir}...")
            return {"working_dir": working_dir}
        except Exception as exc:  # noqa: BLE001
            error_msg = f"Failed to clone {repo_url}: {exc}"
            click.echo(f"❌ {error_msg}", err=True)
            return failure_update(state, error_msg, mode)

    return _node
