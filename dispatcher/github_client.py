"""
GitHub App authentication client for repository operations.

This module handles GitHub App token generation and provides methods for
common GitHub API operations (clone, push, PR creation).
"""

import os
import re
import subprocess
import time
from typing import Optional

import jwt
import requests


class GitHubAuthError(Exception):
    """Raised when GitHub authentication or API calls fail."""

    pass


def push_branch_with_token(
    working_dir: str,
    owner: str,
    repo: str,
    branch: str,
    token: str,
) -> None:
    """Push a branch using a short-lived GitHub App token.

    This temporarily rewrites origin to a tokenized HTTPS URL, pushes the branch,
    and always resets origin back to the public HTTPS URL.

    Raises:
        GitHubAuthError: if set-url, push, or reset-url fails.
    """
    remote_url_with_token = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
    public_remote_url = f"https://github.com/{owner}/{repo}.git"

    try:
        set_url_result = subprocess.run(
            [
                "git",
                "-C",
                working_dir,
                "remote",
                "set-url",
                "origin",
                remote_url_with_token,
            ],
            capture_output=True,
            text=True,
        )
        if set_url_result.returncode != 0:
            raise GitHubAuthError(
                "git remote set-url failed: "
                f"{set_url_result.stderr.strip() or set_url_result.returncode}"
            )

        push_result = subprocess.run(
            [
                "git",
                "-C",
                working_dir,
                "push",
                "origin",
                branch,
            ],
            capture_output=True,
            text=True,
        )
        if push_result.returncode != 0:
            raise GitHubAuthError(
                f"git push failed: {push_result.stderr.strip() or push_result.returncode}"
            )
    finally:
        reset_url_result = subprocess.run(
            [
                "git",
                "-C",
                working_dir,
                "remote",
                "set-url",
                "origin",
                public_remote_url,
            ],
            capture_output=True,
            text=True,
        )
        if reset_url_result.returncode != 0:
            raise GitHubAuthError(
                "git remote reset-url failed: "
                f"{reset_url_result.stderr.strip() or reset_url_result.returncode}"
            )


def _load_private_key() -> str:
    """Load GitHub App private key from GITHUB_APP_PRIVATE_KEY env var.

    The env var should contain the raw PEM-formatted private key with
    newlines represented as literal \n (will be normalized to actual newlines).

    Returns:
        The private key string in PEM format.

    Raises:
        GitHubAuthError: if GITHUB_APP_PRIVATE_KEY is not set.
    """
    key_raw = os.getenv("GITHUB_APP_PRIVATE_KEY")
    if not key_raw:
        raise GitHubAuthError("GITHUB_APP_PRIVATE_KEY env var is not set")

    # Normalize escaped newlines to actual newlines
    key = key_raw.replace("\\n", "\n")
    return key


def _parse_repo_url(url: str) -> tuple[str, str]:
    """Parse a git repository URL (HTTPS or SSH) and return (owner, repo).

    Args:
        url: HTTPS (https://github.com/owner/repo.git) or
             SSH (git@github.com:owner/repo.git) format.

    Returns:
        Tuple of (owner, repo) without .git suffix.

    Raises:
        GitHubAuthError: if URL format is unrecognized.
    """
    # Try HTTPS: https://github.com/owner/repo.git
    https_match = re.match(r"https://github\.com/([^/]+)/([^/]+?)(\.git)?/?$", url)
    if https_match:
        return https_match.group(1), https_match.group(2)

    # Try SSH: git@github.com:owner/repo.git
    ssh_match = re.match(r"git@github\.com:([^/]+)/([^/]+?)(\.git)?/?$", url)
    if ssh_match:
        return ssh_match.group(1), ssh_match.group(2)

    raise GitHubAuthError(f"Unrecognized repository URL format: {url}")


def get_installation_token(project_key: str = "") -> str:
    """Generate a GitHub App installation access token.

    Loads the GitHub App ID, private key, and installation ID from environment
    variables. Generates a JWT signed with the private key, exchanges it for
    an installation access token via the GitHub API.

    Args:
        project_key: Unused placeholder for future multi-org support.
                     Currently a single GitHub App installation is assumed.

    Returns:
        A short-lived installation access token (valid ~1 hour).

    Raises:
        GitHubAuthError: if credentials are missing or the API call fails.
    """
    app_id = os.getenv("GITHUB_APP_ID")
    installation_id = os.getenv("GITHUB_APP_INSTALLATION_ID")

    if not app_id or not installation_id:
        raise GitHubAuthError("GITHUB_APP_ID and GITHUB_APP_INSTALLATION_ID env vars are required")

    private_key = _load_private_key()

    # Generate JWT
    now = int(time.time())
    payload = {
        "iss": app_id,  # issuer = App ID
        "iat": now,
        "exp": now + 600,  # JWT valid for 10 minutes
    }

    try:
        jwt_token = jwt.encode(payload, private_key, algorithm="RS256")
    except Exception as e:
        raise GitHubAuthError(f"Failed to sign JWT: {e}") from e

    # Exchange JWT for installation access token
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        response = requests.post(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data["token"]
    except requests.RequestException as e:
        raise GitHubAuthError(f"Failed to fetch installation token: {e}") from e


def get_open_pr(owner: str, repo: str, branch: str, token: str) -> Optional[str]:
    """Check if an open PR exists for the given branch and return its URL.

    Args:
        owner: Repository owner (username or org).
        repo: Repository name.
        branch: Feature branch name to search for.
        token: GitHub API authentication token.

    Returns:
        The PR URL (html_url) if found, None otherwise.

    Raises:
        GitHubAuthError: if the API call fails.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
    params = {
        "head": f"{owner}:{branch}",
        "state": "open",
    }
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        prs = response.json()
        if prs:
            return prs[0]["html_url"]
        return None
    except requests.RequestException as e:
        raise GitHubAuthError(f"Failed to fetch open PR: {e}") from e


def create_pr(
    owner: str,
    repo: str,
    head: str,
    base: str,
    title: str,
    body: str,
    token: str,
) -> str:
    """Create a pull request.

    Args:
        owner: Repository owner.
        repo: Repository name.
        head: Head branch name (feature branch).
        base: Base branch name (usually "main").
        title: PR title.
        body: PR description (markdown).
        token: GitHub API authentication token.

    Returns:
        The created PR URL (html_url).

    Raises:
        GitHubAuthError: if the PR creation fails.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
    payload = {
        "title": title,
        "body": body,
        "head": head,
        "base": base,
    }
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data["html_url"]
    except requests.RequestException as e:
        raise GitHubAuthError(f"Failed to create PR: {e}") from e


def add_pr_comment(pr_url: str, body: str, token: str) -> None:
    """Add a comment to an existing pull request.

    Args:
        pr_url: The PR URL (e.g., https://github.com/owner/repo/pull/123).
        body: Comment body (markdown).
        token: GitHub API authentication token.

    Raises:
        GitHubAuthError: if the comment fails to post.
    """
    # Parse PR URL: https://github.com/owner/repo/pull/123
    match = re.match(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)/?$", pr_url)
    if not match:
        raise GitHubAuthError(f"Invalid PR URL: {pr_url}")

    owner, repo, pr_number = match.groups()

    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments"
    payload = {"body": body}
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        raise GitHubAuthError(f"Failed to post PR comment: {e}") from e
