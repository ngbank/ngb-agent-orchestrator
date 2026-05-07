"""
WorkPlan comment formatter for JIRA.

This module formats WorkPlan JSON into human-readable Jira comments with
required sections for reviewer approval.
"""

from typing import Dict


def format_work_plan_comment(work_plan: Dict, ticket_id: str) -> str:
    """
    Format WorkPlan as Jira comment with required sections.

    Args:
        work_plan: WorkPlan dictionary containing plan details
        ticket_id: JIRA ticket key (e.g., 'AOS-39')

    Returns:
        str: Formatted Jira markdown comment
    """
    sections = []

    # Header with version marker
    sections.append("<!-- WorkPlan v1.0 -->")
    sections.append("")

    # Title
    sections.append(f"# 🤖 Agent WorkPlan for {ticket_id}")
    sections.append("")

    # Summary section
    summary = work_plan.get("summary", "No summary provided")
    sections.append(f"*{summary}*")
    sections.append("")

    # Plan Status indicator
    status = work_plan.get("status", "unknown")
    status_emoji = {"pass": "✅", "concerns": "⚠️", "blocked": "🚫"}
    emoji = status_emoji.get(status, "❓")
    sections.append(f"**Status:** {emoji} {status.upper()}")
    sections.append("")

    # Approach section
    sections.append("## 📋 Plan Summary")
    sections.append("")
    approach = work_plan.get("approach", "No approach specified")
    sections.append(approach)
    sections.append("")

    # Task List section
    sections.append("## ✅ Task List")
    sections.append("")
    tasks = work_plan.get("tasks", [])
    if tasks:
        for task in tasks:
            task_id = task.get("id", "?")
            description = task.get("description", "No description")
            files = task.get("files_likely_affected", [])

            sections.append(f"### Task {task_id}")
            sections.append(description)
            if files:
                sections.append("")
                sections.append("*Files likely affected:*")
                for file_path in files:
                    sections.append(f"- {{code}}{file_path}{{code}}")
            sections.append("")
    else:
        sections.append("*No tasks defined*")
        sections.append("")

    # Risks section
    sections.append("## ⚠️ Risks")
    sections.append("")
    risks = work_plan.get("risks", [])
    if risks:
        for risk in risks:
            sections.append(f"- {risk}")
        sections.append("")
    else:
        sections.append("*No risks identified*")
        sections.append("")

    # Questions for Reviewer section
    sections.append("## ❓ Questions for Reviewer")
    sections.append("")
    questions = work_plan.get("questions_for_reviewer", [])
    if questions:
        for question in questions:
            sections.append(f"- {question}")
        sections.append("")
    else:
        sections.append("*No questions*")
        sections.append("")

    # Approval Instructions section
    sections.append("## 🎯 Approval Instructions")
    sections.append("")
    sections.append("To approve this plan and allow the agent to proceed:")
    sections.append("{code}")
    sections.append(f"dispatcher approve {ticket_id}")
    sections.append("{code}")
    sections.append("")
    sections.append("To reject this plan and halt execution:")
    sections.append("{code}")
    sections.append(f"dispatcher reject {ticket_id}")
    sections.append("{code}")
    sections.append("")

    # Footer with metadata
    sections.append("---")
    sections.append(f"*WorkPlan Schema Version: {work_plan.get('schema_version', 'unknown')}*")

    return "\n".join(sections)


def format_execution_summary_comment(execution_summary: Dict) -> str:
    """
    Format an execution summary as a Jira comment.

    Args:
        execution_summary: Execution summary dict from the execute recipe

    Returns:
        str: Formatted Jira markdown comment
    """
    sections = []

    status = execution_summary.get("status", "unknown")
    status_emoji = {"success": "✅", "partial": "⚠️", "failed": "❌"}
    emoji = status_emoji.get(status, "❓")

    sections.append(f"# {emoji} Execution Summary")
    sections.append("")

    branch = execution_summary.get("branch", "")
    if branch:
        sections.append(f"*Branch:* {{code}}{branch}{{code}}")
        sections.append("")

    sections.append(f"*Status:* {status.upper()}")
    sections.append(f"*Build:* {execution_summary.get('build', 'unknown')}")
    sections.append(f"*Tests:* {execution_summary.get('tests', 'unknown')}")
    sections.append("")

    files_changed = execution_summary.get("files_changed", [])
    if files_changed:
        sections.append("*Files changed:*")
        for f in files_changed:
            sections.append(f"- {{code}}{f}{{code}}")
        sections.append("")

    commit_sha = execution_summary.get("commit_sha", "")
    if commit_sha:
        sections.append(f"*Commit:* {{code}}{commit_sha[:12]}{{code}}")
        sections.append("")

    pr_url = execution_summary.get("pr_url", "")
    if pr_url:
        sections.append(f"*Pull Request:* [{pr_url}|{pr_url}]")
        sections.append("")

    error = execution_summary.get("error", "")
    if error:
        sections.append(f"*Error:* {error}")
        sections.append("")

    return "\n".join(sections)
