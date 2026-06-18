"""Reusable repo setup primitives shared by planner and executor subgraphs."""

import os
import shutil
import tempfile
from typing import Optional, TextIO

from dispatcher.github_client import GitHubAuthError, get_installation_token
from mcp_server.server import get_repo_for_project
from orchestrator.utils import run_and_tee


def extract_project_key(ticket_key: str) -> str:
    """Extract the project prefix from a JIRA ticket key."""
    return ticket_key.split("-")[0].upper() if ticket_key else ""


def resolve_repository_url(ticket_key: str, repo_url: Optional[str] = None) -> str:
    """Resolve repo URL from explicit value or project mapping."""
    explicit_repo_url = (repo_url or "").strip()
    if explicit_repo_url:
        return explicit_repo_url

    project_key = extract_project_key(ticket_key)
    return get_repo_for_project(project_key)


def fetch_token_for_repo(ticket_key: str, repo_url: str) -> Optional[str]:
    """Fetch a GitHub installation token when HTTPS auth is needed."""
    if not repo_url:
        raise ValueError("Repository URL is required before fetching GitHub token")

    if repo_url.startswith("git@"):
        return None

    project_key = extract_project_key(ticket_key)
    try:
        return get_installation_token(project_key)
    except GitHubAuthError as exc:
        raise RuntimeError(f"GitHub token fetch failed: {exc}") from exc


def clone_repository(
    repo_url: str,
    github_token: Optional[str],
    *,
    temp_prefix: str,
    log_file: TextIO,
) -> str:
    """Clone repository into a newly-created temp directory and return it."""
    if not repo_url:
        raise ValueError("Repository URL is required before cloning")

    working_dir = tempfile.mkdtemp(prefix=temp_prefix)

    clone_url = repo_url
    if repo_url.startswith("https://github.com/") and github_token:
        clone_url = repo_url.replace(
            "https://github.com/",
            f"https://x-access-token:{github_token}@github.com/",
        )

    result = run_and_tee(["git", "clone", clone_url, working_dir], log_file)
    if result.returncode != 0:
        raise RuntimeError(f"git clone exited with code {result.returncode}")

    return working_dir


def cleanup_working_dir(working_dir: Optional[str]) -> bool:
    """Remove working directory if it exists.

    Returns True when a directory existed and cleanup was attempted.
    """
    if working_dir and os.path.isdir(working_dir):
        shutil.rmtree(working_dir, ignore_errors=True)
        return True
    return False
