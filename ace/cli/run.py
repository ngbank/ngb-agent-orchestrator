"""ACE CLI entrypoint.

Thin scaffold mirroring ``dispatcher/run.py``. Subcommands (``mine``,
``items``, ``promote``, ``reject``, ``stats``, ``ontology``) are wired up
starting in Epic 3, ticket 3.1.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Optional

import click

if TYPE_CHECKING:
    from ace.protocols import AgentContextEngineService


def _resolve_service(ctx: click.Context) -> "AgentContextEngineService":
    """Return the AgentContextEngineService for this invocation.

    Tests inject a fake via ``runner.invoke(cli, args, obj=fake_service)``;
    production builds a :class:`~ace.local_service.LocalAgentContextEngineService`
    through :func:`~ace.local_service.build_local_agent_context_engine_service`.
    The service is built lazily so commands that do not need it (``--help``)
    avoid the import / setup cost.
    """
    if ctx.obj is not None:
        return ctx.obj
    from ace.local_service import build_local_agent_context_engine_service

    service = build_local_agent_context_engine_service()
    ctx.obj = service
    return service


@click.group()
@click.pass_context
def cli(ctx: click.Context) -> None:
    """ACE (Agent Context Engine) CLI."""
    # ctx.obj is reserved for service injection (tests) or lazy resolution.


@cli.command()
@click.pass_context
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Maximum number of eligible workflows to process",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Evaluate and reflect but skip all DB writes",
)
@click.option(
    "--workflow-id",
    default=None,
    help="Process a single specific workflow",
)
def mine(
    ctx: click.Context,
    limit: Optional[int],
    dry_run: bool,
    workflow_id: Optional[str],
) -> None:
    """Run the offline mining pipeline."""
    service = _resolve_service(ctx)
    from ace.cli.commands.mine import _handle_mine

    _handle_mine(service, limit=limit, dry_run=dry_run, workflow_id=workflow_id)


def run() -> None:
    """Entry point for the ``ace`` CLI."""
    cli()


if __name__ == "__main__":
    run()
