"""
WorkPlan comment formatter for JIRA.

This module formats WorkPlan JSON into human-readable Jira comments with
required sections for reviewer approval.
"""

from typing import Dict


def _format_work_plan(work_plan: Dict, ticket_id: str) -> str:
    """
    Format WorkPlan as Jira comment with required sections.

    Args:
        work_plan: WorkPlan dictionary containing plan details
        ticket_id: JIRA ticket key (e.g., 'AOS-39')

    Returns:
        str: Formatted Jira markdown comment

    Required sections in output:
    - Plan Summary (approach and status)
    - Task List
    - Concerns
    - Approval Instructions
    """
    sections = []

    sections.append("<!-- WorkPlan v1.0 -->")
    sections.append("")

    sections.append(f"# Agent WorkPlan for {ticket_id}")
    sections.append("")

    summary = work_plan.get("summary", "No summary provided")
    status = work_plan.get("status", "unknown")
    sections.append(f"**Status:** {status.upper()} — *{summary}*")
    sections.append("")

    sections.append("---")
    sections.append("Approach")
    sections.append("---")
    sections.append(work_plan.get("approach", "No approach specified"))
    sections.append("")

    sections.append("---")
    sections.append("Tasks")
    sections.append("---")
    tasks = work_plan.get("tasks", [])
    if tasks:
        for task in tasks:
            task_id = task.get("id", "?")
            description = task.get("description", "No description")
            files = task.get("files_likely_affected", [])
            sections.append(f"**{task_id}. {description}**")
            if files:
                inline_files = ", ".join(f"`{file_path}`" for file_path in files)
                sections.append(f"Files: {inline_files}")
            else:
                sections.append("Files: _None_")
            sections.append("")
    else:
        sections.append("_No tasks defined_")
        sections.append("")

    sections.append("---")
    sections.append("Concerns")
    sections.append("---")
    concerns = work_plan.get("concerns", [])
    if concerns:
        for concern in concerns:
            sections.append(f"- {concern}")
    else:
        sections.append("_No concerns identified_")
    sections.append("")

    sections.append("---")
    sections.append("Approval")
    sections.append("---")
    sections.append(f"Approve: `dispatcher approve {ticket_id}`")
    sections.append(f"Reject: `dispatcher reject {ticket_id}`")
    sections.append("")

    sections.append("---")
    sections.append(f"*WorkPlan Schema Version: {work_plan.get('schema_version', 'unknown')}*")

    return "\n".join(sections)


def format_code_generation_summary_comment(code_generation_summary: Dict) -> str:
    """
    Format a code generation summary as a Jira comment.

    Args:
        code_generation_summary: Code generation summary dict from the generate_code recipe

    Returns:
        str: Formatted Jira markdown comment
    """
    sections = []

    status = code_generation_summary.get("status", "unknown")
    status_emoji = {"success": "✅", "partial": "⚠️", "failed": "❌"}
    emoji = status_emoji.get(status, "❓")

    sections.append(f"# {emoji} Code Generation Summary")
    sections.append("")

    branch = code_generation_summary.get("branch", "")
    if branch:
        sections.append(f"*Branch:* {{code}}{branch}{{code}}")
        sections.append("")

    sections.append(f"*Status:* {status.upper()}")
    sections.append(f"*Build:* {code_generation_summary.get('build', 'unknown')}")
    sections.append(f"*Tests:* {code_generation_summary.get('tests', 'unknown')}")
    sections.append("")

    files_changed = code_generation_summary.get("files_changed", [])
    if files_changed:
        sections.append("*Files changed:*")
        for f in files_changed:
            sections.append(f"- {{code}}{f}{{code}}")
        sections.append("")

    commit_sha = code_generation_summary.get("commit_sha", "")
    if commit_sha:
        sections.append(f"*Commit:* {{code}}{commit_sha[:12]}{{code}}")
        sections.append("")

    pr_url = code_generation_summary.get("pr_url", "")
    if pr_url:
        sections.append(f"*Pull Request:* [{pr_url}|{pr_url}]")
        sections.append("")

    error = code_generation_summary.get("error", "")
    if error:
        sections.append(f"*Error:* {error}")
        sections.append("")

    return "\n".join(sections)


def format_work_plan_comment(work_plan: Dict, ticket_id: str) -> str:
    """
    Convenience function to format a WorkPlan as a Jira comment.

    Args:
        work_plan: WorkPlan dictionary
        ticket_id: JIRA ticket key

    Returns:
        str: Formatted Jira markdown comment

    Example:
        >>> work_plan = {
        ...     'schema_version': '1.0',
        ...     'ticket_key': 'AOS-39',
        ...     'summary': 'Implement comment posting',
        ...     'approach': 'Add ACLI integration',
        ...     'tasks': [
        ...         {
        ...             'id': 1, 'description': 'Create formatter',
        ...             'files_likely_affected': ['formatter.py']
        ...         }
        ...     ],
        ...     'concerns': [
        ...         'Is ACLI installed on the runner, or should the recipe install it? '
        ...         'This determines whether we add a bootstrap step.'
        ...     ],
        ...     'status': 'concerns'
        ... }
        >>> comment = format_work_plan_comment(work_plan, 'AOS-39')
        >>> print(comment)
    """
    return _format_work_plan(work_plan, ticket_id)
