"""Handler for the main --ticket workflow run."""

import logging
import sys
import uuid
from contextlib import contextmanager
from typing import TYPE_CHECKING

import click

import dispatcher.commands.common as common
from dispatcher.commands.follow import submit_and_follow
from dispatcher.exceptions import TicketAuthError, TicketConfigError, TicketNotFoundError
from orchestrator.workflow_service import WorkflowStartRequest
from state.workflow_status import WorkflowStatus

if TYPE_CHECKING:
    from orchestrator.workflow_service import WorkflowService


class _ClickLogHandler(logging.Handler):
    """Emit workflow log records through Click for interactive CLI runs."""

    def emit(self, record: logging.LogRecord) -> None:
        click.echo(self.format(record))


@contextmanager
def _workflow_cli_logs():
    """Echo workflow log records to the terminal through Click, once each.

    ``setup_logging`` installs a timestamped console ``StreamHandler`` bound
    to the process's original stderr. Subprocess output (Goose, git) is
    routed through the same root logger, so leaving that handler attached
    here would print every line twice: once with the raw timestamp/logger
    prefix, once more through this handler's plain Click echo. Detach it for
    the duration of the run so each line prints once; ``workflow.log`` still
    captures the full timestamped record independently via its own
    ``WorkflowFileHandler``.
    """
    handler = _ClickLogHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))

    root = logging.getLogger()
    original_level = root.level
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)

    console_handlers = [
        h
        for h in root.handlers
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
    ]
    for h in console_handlers:
        root.removeHandler(h)

    root.addHandler(handler)
    try:
        yield
    finally:
        root.removeHandler(handler)
        for h in console_handlers:
            root.addHandler(h)
        root.setLevel(original_level)


def _handle_run(
    service: "WorkflowService",
    ticket: str,
    dry_run: bool,
    detach: bool = False,
) -> None:
    click.echo(f"🚀 Starting workflow for ticket: {ticket}")

    if dry_run:
        click.echo("[DRY RUN] Mode enabled - no changes will be made")
        click.echo(f"[DRY RUN] Would fetch ticket: {ticket}")
        click.echo("[DRY RUN] Would check for duplicate workflows")
        click.echo(f"[DRY RUN] Would create workflow for ticket: {ticket}")
        click.echo("[DRY RUN] Would execute workflow stages")
        click.echo("✅ Dry run completed successfully")
        return

    # Pre-generate a UUID that acts as both the workflow DB ID and the
    # LangGraph thread_id, keeping the two systems in sync.
    workflow_id = str(uuid.uuid4())

    try:
        with _workflow_cli_logs():
            result = submit_and_follow(
                service,
                service.start,
                WorkflowStartRequest(ticket_key=ticket, workflow_id=workflow_id),
                workflow_id_hint=workflow_id,
                detach=detach,
            )

        if result.error:
            sys.exit(1)

        if result.interrupted:
            # Graph suspended at await_approval — the node already printed the
            # approval instructions.
            return

        if result.final_status == WorkflowStatus.COMPLETED:
            click.echo("🎉 Workflow completed successfully")
            common._post_execution_comment(ticket, result.code_generation_summary)
            return

        # Graph paused at await_approval without raising GraphInterrupt
        # (uncommon, but possible) — nothing more to do here.

    except TicketNotFoundError as e:
        click.echo(f"❌ Ticket not found: {e}", err=True)
        sys.exit(1)

    except TicketConfigError as e:
        click.echo(f"❌ JIRA configuration error: {e}", err=True)
        click.echo("   Required values:", err=True)
        click.echo("     - JIRA_URL (from Azure Key Vault)", err=True)
        click.echo("     - JIRA_OAUTH_CLIENT_ID (from Azure Key Vault)", err=True)
        click.echo(
            "     - JIRA_OAUTH_CLIENT_SECRET (from Azure Key Vault)",
            err=True,
        )
        click.echo(
            "     - Optional: JIRA_OAUTH_TOKEN_URL "
            "(Atlassian Cloud default: https://auth.atlassian.com/oauth/token)",
            err=True,
        )
        click.echo(
            "     - Optional: JIRA_OAUTH_AUDIENCE (some providers require this)",
            err=True,
        )
        click.echo(
            "       Atlassian Cloud token URL: https://auth.atlassian.com/oauth/token", err=True
        )
        click.echo("   If this is a fresh shell, run: direnv reload", err=True)
        sys.exit(1)

    except TicketAuthError as e:
        click.echo(f"❌ JIRA authentication error: {e}", err=True)
        click.echo(
            "   Verify OAuth client credentials and service-account permissions in JIRA.", err=True
        )
        sys.exit(1)

    except KeyboardInterrupt:
        click.echo("\n⚠️  Workflow interrupted by user", err=True)
        service.mark_interrupted(workflow_id, actor="dispatcher")
        click.echo(
            f"⚠️  Marked workflow {workflow_id} as FAILED. "
            f"Resume with: dispatcher --retry --workflow-id {workflow_id}",
            err=True,
        )
        sys.exit(130)

    except Exception as e:
        click.echo(f"❌ Unhandled error: {e}", err=True)
        sys.exit(1)
