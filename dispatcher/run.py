#!/usr/bin/env python3
"""
Agent Orchestrator Dispatcher

Main entry point for workflow orchestration. Fetches JIRA tickets, creates
workflow records, and orchestrates the complete workflow lifecycle.

Usage:
    python dispatcher/run.py --ticket AOS-36
    python dispatcher/run.py --ticket AOS-36 --dry-run
"""

import sys
import click
from typing import Optional
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from dispatcher.jira_client import (
    JiraClient,
    JiraTicket,
    JiraConfigurationError,
    JiraAuthenticationError,
    JiraTicketNotFoundError,
)
from state.state_store import (
    create_workflow,
    update_status,
    get_workflow_by_ticket,
)
from state.workflow_status import WorkflowStatus
from dispatcher.work_plan_validator import (
    validate_work_plan,
    WorkPlan,
    WorkPlanValidationError,
)


def check_for_duplicate_workflow(ticket_key: str) -> Optional[str]:
    """
    Check if a workflow is already running for this ticket.
    
    Args:
        ticket_key: JIRA ticket key (e.g., "AOS-36")
    
    Returns:
        workflow_id if duplicate detected (in-flight workflow exists)
        None if safe to proceed
    """
    workflows = get_workflow_by_ticket(ticket_key)
    
    # Check for any pending or in_progress workflows
    completed_count = 0
    
    for workflow in workflows:
        if workflow['status'].is_active():
            return workflow['id']
        if workflow['status'] == WorkflowStatus.COMPLETED:
            completed_count += 1
    
    # Warn if re-running completed workflow
    if completed_count > 0:
        click.echo(f"⚠️  Warning: {completed_count} completed workflow(s) exist for {ticket_key}")
        click.echo("   Creating new workflow run...")
    
    return None


def execute_workflow(
    workflow_id: str,
    ticket: JiraTicket,
    dry_run: bool,
    work_plan_data: dict | None = None,
) -> None:
    """
    Execute workflow stages in sequence.
    
    Args:
        workflow_id: UUID of the workflow
        ticket: JIRA ticket data
        dry_run: If True, only print actions without executing
        work_plan_data: Raw WorkPlan dict from the planner. If provided,
            it is validated against the schema before execution proceeds.
    
    TODO: Future tickets will implement:
    - Code execution via Goose
    - PR creation
    """
    # Transition to in_progress
    if not dry_run:
        update_status(
            workflow_id,
            WorkflowStatus.IN_PROGRESS,
            actor='dispatcher',
            reason='Starting workflow execution'
        )
    
    if dry_run:
        click.echo(f"[DRY RUN] Would execute workflow stages for {ticket.key}")
        click.echo(f"[DRY RUN]   Title: {ticket.title}")
        click.echo(f"[DRY RUN]   Status: {ticket.status}")
        if work_plan_data:
            click.echo(f"[DRY RUN] Would validate WorkPlan against schema")
        return

    click.echo(f"📋 Workflow {workflow_id} created for ticket {ticket.key}")
    click.echo(f"   Title: {ticket.title}")
    click.echo(f"   Status: {ticket.status}")
    click.echo(f"   Labels: {', '.join(ticket.labels) if ticket.labels else 'none'}")

    # Stage 1: Validate WorkPlan
    work_plan: WorkPlan | None = None
    if work_plan_data is not None:
        click.echo("🔍 Validating WorkPlan...")
        try:
            work_plan = validate_work_plan(work_plan_data)
            click.echo(f"✅ WorkPlan validated (status: {work_plan.status})")
            if work_plan.status == 'blocked':
                update_status(
                    workflow_id,
                    WorkflowStatus.FAILED,
                    actor='dispatcher',
                    reason='Planner marked WorkPlan as blocked'
                )
                click.echo("❌ WorkPlan status is 'blocked' — workflow cannot proceed.", err=True)
                return
        except WorkPlanValidationError as e:
            update_status(
                workflow_id,
                WorkflowStatus.FAILED,
                actor='dispatcher',
                reason=str(e)
            )
            click.echo(f"❌ {e}", err=True)
            return

    # Stage 2: Execute code (Goose integration — future ticket)
    # Stage 3: Create PR (future ticket)

    click.echo("✅ Workflow stages completed (placeholder)")


@click.command()
@click.option(
    '--ticket',
    required=True,
    help='JIRA ticket key (e.g., AOS-36)'
)
@click.option(
    '--dry-run',
    is_flag=True,
    help='Print actions without executing (no API calls or database changes)'
)
def run(ticket: str, dry_run: bool):
    """
    Main dispatcher entry point for workflow orchestration.
    
    Fetches the specified JIRA ticket, creates a workflow record, and orchestrates
    the complete workflow lifecycle including plan generation, code execution,
    and PR creation.
    
    Examples:
    
        # Run a workflow for a ticket
        python dispatcher/run.py --ticket AOS-36
        
        # Preview what would happen without executing
        python dispatcher/run.py --ticket AOS-36 --dry-run
    """
    workflow_id = None
    
    try:
        # Validate ticket format (basic check)
        if not ticket or '-' not in ticket:
            click.echo("❌ Invalid ticket format. Expected format: PROJECT-123", err=True)
            sys.exit(1)
        
        click.echo(f"🚀 Starting workflow for ticket: {ticket}")
        
        # Check for dry-run mode early
        if dry_run:
            click.echo("[DRY RUN] Mode enabled - no changes will be made")
            click.echo(f"[DRY RUN] Would fetch ticket: {ticket}")
            
            # Still validate environment configuration
            try:
                JiraClient()
            except (JiraConfigurationError, JiraAuthenticationError) as e:
                click.echo(f"❌ JIRA configuration error: {e}", err=True)
                click.echo("   (Detected in dry-run mode - would fail in real execution)", err=True)
                sys.exit(1)
            
            click.echo(f"[DRY RUN] Would check for duplicate workflows")
            click.echo(f"[DRY RUN] Would create workflow for ticket: {ticket}")
            click.echo(f"[DRY RUN] Would execute workflow stages")
            click.echo(f"[DRY RUN] Would transition to completed")
            click.echo("✅ Dry run completed successfully")
            return
        
        # Initialize JIRA client
        click.echo("🔧 Initializing JIRA client...")
        jira_client = JiraClient()
        
        # Fetch ticket
        click.echo(f"📥 Fetching ticket {ticket}...")
        jira_ticket = jira_client.get_ticket(ticket)
        click.echo(f"✅ Ticket fetched: {jira_ticket.title}")
        
        # Check for duplicate workflows
        click.echo("🔍 Checking for duplicate workflows...")
        duplicate_workflow_id = check_for_duplicate_workflow(ticket)
        if duplicate_workflow_id:
            click.echo(
                f"❌ Workflow already in progress for {ticket} (ID: {duplicate_workflow_id})",
                err=True
            )
            click.echo("   Cannot start a new workflow while one is active.", err=True)
            click.echo("   Wait for the current workflow to complete or fail.", err=True)
            sys.exit(1)
        
        # Create workflow
        click.echo("📝 Creating workflow record...")
        workflow_id = create_workflow(
            ticket_key=ticket,
            work_plan=None,  # Will be populated by future plan generation stage
            status=WorkflowStatus.PENDING
        )
        click.echo(f"✅ Workflow created: {workflow_id}")
        
        # Execute stages
        click.echo("⚙️  Executing workflow stages...")
        execute_workflow(workflow_id, jira_ticket, dry_run=False)
        
        # Mark as completed
        click.echo("🎉 Workflow completed successfully")
        update_status(
            workflow_id,
            WorkflowStatus.COMPLETED,
            actor='dispatcher',
            reason='All stages completed successfully'
        )
        
    except JiraTicketNotFoundError as e:
        click.echo(f"❌ Ticket not found: {e}", err=True)
        if workflow_id:
            update_status(
                workflow_id,
                WorkflowStatus.FAILED,
                actor='dispatcher',
                reason=f"Ticket not found: {e}"
            )
        sys.exit(1)
        
    except JiraConfigurationError as e:
        click.echo(f"❌ JIRA configuration error: {e}", err=True)
        click.echo("   Please check your environment variables:", err=True)
        click.echo("     - JIRA_URL", err=True)
        click.echo("     - JIRA_EMAIL", err=True)
        click.echo("     - JIRA_API_TOKEN", err=True)
        sys.exit(1)
        
    except JiraAuthenticationError as e:
        click.echo(f"❌ JIRA authentication error: {e}", err=True)
        click.echo("   Please verify your credentials are correct.", err=True)
        if workflow_id:
            update_status(
                workflow_id,
                WorkflowStatus.FAILED,
                actor='dispatcher',
                reason=f"JIRA authentication failed: {e}"
            )
        sys.exit(1)
        
    except KeyboardInterrupt:
        click.echo("\n⚠️  Workflow interrupted by user", err=True)
        if workflow_id:
            update_status(
                workflow_id,
                WorkflowStatus.FAILED,
                actor='dispatcher',
                reason='Workflow interrupted by user (SIGINT)'
            )
        sys.exit(130)  # Standard exit code for SIGINT
        
    except Exception as e:
        click.echo(f"❌ Unhandled error: {e}", err=True)
        if workflow_id:
            update_status(
                workflow_id,
                WorkflowStatus.FAILED,
                actor='dispatcher',
                reason=f"Unhandled exception: {type(e).__name__}: {str(e)}"
            )
        # Re-raise for debugging if needed (comment out for production)
        # raise
        sys.exit(1)


if __name__ == '__main__':
    run()
