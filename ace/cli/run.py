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


if __name__ == "__main__":
    run()
