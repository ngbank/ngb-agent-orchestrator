"""ACE CLI entrypoint.

Thin CLI entrypoint mirroring ``dispatcher/run.py``.  All ACE command logic
lives under ``ace/cli/commands/``.  This module is responsible only for:

  - Parsing CLI arguments
  - Constructing (or accepting via ``ctx.obj``) an ``AgentContextEngineService``
  - Dispatching to the appropriate command handler (lazily loaded)

Each handler submodule is imported only when the relevant command is actually
invoked, which keeps ``ace --help`` near-instant.  The service is also
constructed lazily so light-weight invocations (``--help``) do not pay for
repository setup.

Usage:
    ace mine
    ace mine --dry-run
    ace mine --limit 10
    ace mine --workflow-id <uuid>
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import click

if TYPE_CHECKING:
    from ace.service.protocols import AgentContextEngineService


def _resolve_service(ctx: click.Context) -> "AgentContextEngineService":
    """Return the AgentContextEngineService for this invocation.

    Tests inject a fake via ``runner.invoke(run, args, obj=fake_service)``;
    production builds the local implementation via
    :func:`ace.service.factory.build_local_agent_context_engine_service`.
    The service is built lazily so commands that do not need it (``--help``)
    avoid the import / setup cost.
    """
    if ctx.obj is not None:
        return ctx.obj
    from ace.service.factory import build_local_agent_context_engine_service

    service = build_local_agent_context_engine_service()
    ctx.obj = service
    return service


@click.group()
@click.pass_context
def run(ctx: click.Context) -> None:
    """ACE CLI — Agentic Context Engine."""


@run.command()
@click.pass_context
@click.option(
    "--dry-run",
    is_flag=True,
    help="Evaluate and reflect but skip all DB writes",
)
@click.option(
    "--limit",
    default=None,
    type=int,
    help="Cap the number of workflows fetched (ignored with --workflow-id)",
)
@click.option(
    "--workflow-id",
    "workflow_id",
    default=None,
    help="Process a single specific workflow, bypassing eligibility anti-join",
)
def mine(
    ctx: click.Context,
    dry_run: bool,
    limit: Optional[int],
    workflow_id: Optional[str],
) -> None:
    """Run the mining pipeline over eligible workflows."""
    service = _resolve_service(ctx)

    from ace.cli.commands.mine import _handle_mine

    _handle_mine(service, dry_run=dry_run, limit=limit, workflow_id=workflow_id)


if __name__ == "__main__":
    run()
