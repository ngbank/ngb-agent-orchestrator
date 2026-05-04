"""Node: post_to_jira — format and post the WorkPlan as a JIRA comment."""

import click

from dispatcher.jira_client import JiraClient, JiraCommentError
from dispatcher.work_plan_formatter import format_work_plan_comment
from graph.work_planner.state import WorkPlannerState


def post_to_jira(state: WorkPlannerState) -> dict:
    work_plan_data = state.get("work_plan_data")
    if not work_plan_data:
        return {}

    ticket_key = state.get("ticket_key", "")
    click.echo("📝 Posting WorkPlan to Jira...")
    try:
        comment_text = format_work_plan_comment(work_plan_data, ticket_key)
        jira_client = JiraClient()
        success = jira_client.post_comment(ticket_key, comment_text)
        if success:
            click.echo("✅ WorkPlan posted to Jira")
            click.echo(f"   View at: {jira_client.jira_url}/browse/{ticket_key}")
        else:
            click.echo("⚠️  WorkPlan posted but confirmation unclear", err=True)
    except JiraCommentError as e:
        click.echo(f"⚠️  Failed to post WorkPlan to Jira: {e}", err=True)
        click.echo(
            "   WorkPlan is stored in SQLite. You can retry posting manually.",
            err=True,
        )
    except Exception as e:
        click.echo(f"⚠️  Unexpected error posting to Jira: {e}", err=True)

    return {}
