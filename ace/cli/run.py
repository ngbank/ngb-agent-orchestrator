#!/usr/bin/env python3
"""
ACE CLI entrypoint.

Thin CLI mirroring ``dispatcher/run.py``.  All command logic lives under
``ace/cli/commands/``.  This module is responsible only for:

  - Defining the ``ace`` command group and its subcommands' flags
  - Constructing (or accepting via ``ctx.obj``) an :class:`AgentContextEngineService`
  - Dispatching to the appropriate command handler (lazily loaded)

Each handler submodule is imported only when the relevant command is invoked,
which keeps ``ace --help`` and ``ace <verb> --help`` near-instant.  The
service is also constructed lazily so light-weight invocations do not pay for
repository / DB setup.

Usage::

    ace mine
    ace mine --dry-run
    ace mine --limit 5
    ace mine --workflow-id <uuid>
    ace items list
    ace items list --status staged --tier ESTABLISHED
    ace items show <item-id>
    ace promote <item-id>
    ace promote <item-id> --notes "Looks good" --scope task_type --scope-value migration
    ace reject <item-id>
    ace reject <item-id> --notes "Insufficient evidence"
    ace stats
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Optional

import click
from dotenv import load_dotenv

from orchestrator.logging_setup import setup_logging
from orchestrator.runtime_secrets import load_runtime_secrets_from_keyvault

if TYPE_CHECKING:
    from ace.service import AgentContextEngineService

load_dotenv()
load_runtime_secrets_from_keyvault()

# Initialise logging based on ``LOG_LEVEL`` environment variable.
setup_logging()


def _resolve_service(ctx: click.Context) -> "AgentContextEngineService":
    """Return the :class:`AgentContextEngineService` for this invocation.

    Tests inject a fake via ``runner.invoke(run, args, obj=fake_service)``;
    production picks the implementation from ``ACE_MODE`` (defaults to
    ``local``), wired through
    :func:`build_agent_context_engine_service_from_env`.  The service is built
    lazily so commands that do not need it (``--help``) avoid the import /
    setup cost.
    """
    if ctx.obj is not None:
        return ctx.obj
    from ace.service import build_agent_context_engine_service_from_env

    try:
        service = build_agent_context_engine_service_from_env()
    except ValueError as exc:
        click.echo(f"\u274c {exc}", err=True)
        sys.exit(2)
    ctx.obj = service
    return service


@click.group()
def run() -> None:
    """ACE — Agentic Context Engine CLI."""


@run.command("mine")
@click.pass_context
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Maximum number of eligible workflows to process (ignored with --workflow-id).",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Evaluate and reflect but skip all DB writes.",
)
@click.option(
    "--workflow-id",
    "workflow_id",
    default=None,
    metavar="UUID",
    help=(
        "Process only this specific workflow, bypassing the eligibility anti-join. "
        "Useful for re-running after a pipeline failure."
    ),
)
def mine(
    ctx: click.Context,
    limit: Optional[int],
    dry_run: bool,
    workflow_id: Optional[str],
) -> None:
    """Run the offline mining pipeline over eligible workflows."""
    service = _resolve_service(ctx)
    from ace.cli.commands.mine import _handle_mine

    _handle_mine(service, limit=limit, dry_run=dry_run, workflow_id=workflow_id)


# ---------------------------------------------------------------------------
# ace items
# ---------------------------------------------------------------------------


@run.group("items")
@click.pass_context
def items(ctx: click.Context) -> None:
    """Inspect context items (live and staged)."""


@items.command("list")
@click.pass_context
@click.option(
    "--status",
    default=None,
    type=click.Choice(["active", "staged", "deprecated", "conflicted"], case_sensitive=False),
    help="Filter by item status.  Omit to show all live items.",
)
@click.option(
    "--pattern-type",
    "pattern_type",
    default=None,
    type=click.Choice(
        ["approach", "concern", "test_coverage", "implementation"], case_sensitive=False
    ),
    help="Filter by pattern type.",
)
@click.option(
    "--scope",
    default=None,
    type=click.Choice(["task_type", "file_pattern", "codebase_wide"], case_sensitive=False),
    help="Filter by scope dimension.",
)
@click.option(
    "--tier",
    "confidence_tier",
    default=None,
    type=click.Choice(["ESTABLISHED", "PATTERN", "TENTATIVE"], case_sensitive=False),
    help="Filter by confidence tier.",
)
def items_list(
    ctx: click.Context,
    status: Optional[str],
    pattern_type: Optional[str],
    scope: Optional[str],
    confidence_tier: Optional[str],
) -> None:
    """List context items, optionally filtered."""
    service = _resolve_service(ctx)
    from ace.cli.commands.items import _handle_items_list

    _handle_items_list(
        service,
        status=status,
        pattern_type=pattern_type,
        scope=scope,
        confidence_tier=confidence_tier.upper() if confidence_tier else None,
    )


@items.command("show")
@click.pass_context
@click.argument("item_id", metavar="ITEM_ID")
def items_show(ctx: click.Context, item_id: str) -> None:
    """Show full detail for one context item including its provenance chain."""
    service = _resolve_service(ctx)
    from ace.cli.commands.items import _handle_items_show

    _handle_items_show(service, item_id=item_id)


# ---------------------------------------------------------------------------
# ace promote
# ---------------------------------------------------------------------------


@run.command("promote")
@click.pass_context
@click.argument("item_id", metavar="ITEM_ID")
@click.option(
    "--notes",
    default=None,
    metavar="TEXT",
    help="Optional reviewer annotations stored alongside the promoted item.",
)
@click.option(
    "--scope",
    default=None,
    type=click.Choice(["task_type", "file_pattern", "codebase_wide"], case_sensitive=False),
    help="Narrow the item's scope dimension at promotion time.",
)
@click.option(
    "--scope-value",
    "scope_value",
    default=None,
    metavar="VALUE",
    help="Narrow the item's scope value at promotion time.",
)
def promote(
    ctx: click.Context,
    item_id: str,
    notes: Optional[str],
    scope: Optional[str],
    scope_value: Optional[str],
) -> None:
    """Promote a staged context item into the live store.

    Appends a human_review evidence event (+0.20 confidence, capped at 1.0).
    Use --notes to capture review annotations; use --scope / --scope-value to
    narrow the item's applicability before promoting.
    """
    service = _resolve_service(ctx)
    from ace.cli.commands.promote import _handle_promote

    _handle_promote(service, item_id=item_id, notes=notes, scope=scope, scope_value=scope_value)


# ---------------------------------------------------------------------------
# ace reject
# ---------------------------------------------------------------------------


@run.command("reject")
@click.pass_context
@click.argument("item_id", metavar="ITEM_ID")
@click.option(
    "--notes",
    default=None,
    metavar="TEXT",
    help="Optional reviewer annotations stored alongside the rejected item.",
)
def reject(
    ctx: click.Context,
    item_id: str,
    notes: Optional[str],
) -> None:
    """Mark a staged context item as rejected (no hard delete).

    Sets rejected_at on the staged row for audit.  Use --notes to capture
    the reason for rejection.
    """
    service = _resolve_service(ctx)
    from ace.cli.commands.promote import _handle_reject

    _handle_reject(service, item_id=item_id, notes=notes)


# ---------------------------------------------------------------------------
# ace stats
# ---------------------------------------------------------------------------


@run.command("stats")
@click.pass_context
def stats(ctx: click.Context) -> None:
    """Print aggregate ACE store health metrics.

    Reports live-item counts by status, tier, and pattern_type; staging queue
    age; and the item generation rate per mined workflow.
    """
    service = _resolve_service(ctx)
    from ace.cli.commands.stats import _handle_stats

    _handle_stats(service)


if __name__ == "__main__":
    run()
