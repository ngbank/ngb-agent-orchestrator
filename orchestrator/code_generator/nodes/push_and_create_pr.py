"""Node: push_and_create_pr — push branch and open/update PR."""

import subprocess
from pathlib import Path
from typing import Optional

import click

from dispatcher.github_client import (
    GitHubAuthError,
    _parse_repo_url,
    add_pr_comment,
    create_pr,
    get_open_pr,
    push_branch_with_token,
)
from orchestrator.code_generator.state import (
    PushAndCreatePrInputState,
    PushAndCreatePrOutputState,
)


def _read_pr_template(working_dir: str) -> Optional[str]:
    """Read .github/pull_request_template.md if it exists in the repo.

    Args:
        working_dir: Path to the cloned repository.

    Returns:
        Template content if found, None otherwise.
    """
    template_path = Path(working_dir) / ".github" / "pull_request_template.md"
    if template_path.exists():
        try:
            return template_path.read_text()
        except Exception:
            return None
    return None


def _build_pr_body(
    ticket_key: str,
    summary: str,
    work_plan_data: dict,
    template: Optional[str] = None,
) -> str:
    """Build a PR description from the work plan and optional template.

    Args:
        ticket_key: JIRA ticket key.
        summary: WorkPlan summary.
        work_plan_data: Full work plan dict.
        template: Optional PR template to fill in.

    Returns:
        PR body as markdown.
    """
    if template:
        # Use template but fill in key sections
        body = template
        # Replace common placeholders
        body = (
            body.replace("{{ ticket_key }}", ticket_key)
            .replace("{{ summary }}", summary)
            .replace("{{ approach }}", work_plan_data.get("approach", ""))
        )
        return body

    # Minimal fallback if no template
    tasks_str = "\n".join(
        [
            f"- {task.get('description', task.get('id', 'unknown'))}"
            for task in work_plan_data.get("tasks", [])
        ]
    )

    return f"""## Description

{summary}

## JIRA Ticket

[{ticket_key}](https://mirandags.atlassian.net/browse/{ticket_key})

## Changes Made

{tasks_str}

## Approach

{work_plan_data.get('approach', 'See JIRA ticket for details.')}
"""


def push_and_create_pr(
    state: PushAndCreatePrInputState,
) -> PushAndCreatePrOutputState:
    """Push branch and create/update pull request.

        Reads:  ticket_key, working_dir, repo_url, github_token, execution_summary,
            work_plan_data, pr_comments
    Writes: execution_summary (updated with pr_url and status), failed_node
    """
    ticket_key = state.get("ticket_key", "")
    working_dir = state.get("working_dir", "")
    repo_url = state.get("repo_url", "")
    github_token = state.get("github_token", "")
    execution_summary = state.get("execution_summary") or {}
    work_plan_data = state.get("work_plan_data") or {}
    pr_comments = state.get("pr_comments", "")

    # Skip if previous nodes failed
    exec_error = state.get("exec_error")
    if exec_error or execution_summary.get("status") == "failed":
        click.echo("⊘ Skipping push/PR — previous node failed")
        return {
            "execution_summary": execution_summary,
            "failed_node": None,
        }

    # Extract branch, commit SHA from summary
    branch = execution_summary.get("branch", "")
    commit_sha = execution_summary.get("commit_sha", "")
    pr_url = execution_summary.get("pr_url", "")

    if not branch or not commit_sha:
        click.echo("⊘ Skipping push/PR — missing branch or commit SHA", err=True)
        execution_summary["pr_url"] = ""
        return {
            "execution_summary": execution_summary,
            "failed_node": None,
        }

    if not repo_url:
        click.echo("⊘ Skipping push/PR — missing repository URL", err=True)
        execution_summary["pr_url"] = ""
        return {
            "execution_summary": execution_summary,
            "failed_node": None,
        }

    try:
        owner, repo = _parse_repo_url(repo_url)
    except Exception as e:
        click.echo(f"❌ Failed to parse repo URL: {e}", err=True)
        execution_summary["pr_url"] = ""
        return {
            "execution_summary": execution_summary,
            "failed_node": None,
        }

    # When re-executing after PR comments, verify Goose actually produced new commits.
    # If origin/<branch> already points at the same SHA, the branch is unchanged and
    # posting an "Addressed review comments" PR comment would be misleading.
    if pr_comments:
        try:
            rev_result = subprocess.run(
                ["git", "-C", working_dir, "rev-parse", f"origin/{branch}"],
                capture_output=True,
                text=True,
            )
            if rev_result.returncode == 0 and rev_result.stdout.strip() == commit_sha:
                click.echo(
                    "⊘ Re-execution produced no new commits — branch is unchanged",
                    err=True,
                )
                execution_summary["status"] = "failed"
                execution_summary["error"] = (
                    "Re-execution produced no new commits. "
                    "The branch is unchanged — review comments may not have been addressed."
                )
                return {
                    "execution_summary": execution_summary,
                    "failed_node": "execute_plan",
                }
        except Exception:
            pass  # if git isn't callable, fall through and attempt the push normally

    # === PUSH ===
    click.echo(f"📤 Pushing {branch}...")
    try:
        push_branch_with_token(
            working_dir=working_dir,
            owner=owner,
            repo=repo,
            branch=branch,
            token=github_token,
        )
        click.echo(f"✓ Pushed {branch}")
    except GitHubAuthError as e:
        click.echo(f"❌ Failed to push branch: {e}", err=True)
        execution_summary["pr_url"] = ""
        # Downgrade status to "partial" (code was committed but push failed)
        if execution_summary.get("status") == "success":
            execution_summary["status"] = "partial"
        return {
            "execution_summary": execution_summary,
            "failed_node": None,
        }

    # === PR CREATION / UPDATE ===
    try:
        existing_pr_url = get_open_pr(owner, repo, branch, github_token)

        if existing_pr_url:
            click.echo(f"✓ Found existing PR: {existing_pr_url}")
            pr_url = existing_pr_url

            # If re-execution after PR comments, add a summary comment
            if pr_comments:
                click.echo("💬 Adding comment to PR...")
                comment_body = (
                    f"Addressed review comments:\n\n{pr_comments[:200]}..."
                    if len(pr_comments) > 200
                    else f"Addressed review comments:\n\n{pr_comments}"
                )
                try:
                    add_pr_comment(pr_url, comment_body, github_token)
                    click.echo("✓ Posted comment to PR")
                except GitHubAuthError as e:
                    click.echo(f"⚠️  Failed to post comment: {e}", err=True)
        else:
            click.echo("📝 Creating new PR...")
            summary = work_plan_data.get("summary", "")
            template = _read_pr_template(working_dir)
            pr_body = _build_pr_body(ticket_key, summary, work_plan_data, template)
            pr_title = f"[{ticket_key}] {summary}"

            pr_url = create_pr(
                owner,
                repo,
                head=branch,
                base="main",
                title=pr_title,
                body=pr_body,
                token=github_token,
            )
            click.echo(f"✓ Created PR: {pr_url}")

        execution_summary["pr_url"] = pr_url
    except GitHubAuthError as e:
        click.echo(f"❌ Failed to create/update PR: {e}", err=True)
        execution_summary["pr_url"] = ""
        # Downgrade status to "partial" (code was committed and pushed)
        if execution_summary.get("status") == "success":
            execution_summary["status"] = "partial"

    return {
        "execution_summary": execution_summary,
        "failed_node": None,
    }
