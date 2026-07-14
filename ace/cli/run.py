"""ACE CLI entrypoint.

Thin scaffold mirroring ``dispatcher/run.py``.  The service is resolved
lazily so lightweight invocations (``--help``) avoid repository setup cost.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import click

if TYPE_CHECKING:
    from ace.service.protocols import AgentContextEngineService


def _resolve_service(ctx: click.Context) -> "AgentContextEngineService":
    """Return the AgentContextEngineService for this invocation.

    Tests inject a fake via ``runner.invoke(run, args, obj=fake_service)``;
    production builds the local implementation through
    :func:`ace.service.factory.build_local_agent_context_engine_service`.
    The service is built lazily so commands that do not need it (``--help``)
    avoid the import / DB cost.
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
    """ACE (Agent Context Engine) CLI.

    Commands for mining, reviewing, and managing learned context items.
    """
    # ctx.obj is populated lazily by _resolve_service when needed.


@run.command()
@click.pass_context
@click.option(
    "--limit",
    default=None,
    type=int,
    help="Maximum number of eligible workflows to process",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Evaluate and reflect without writing to the database",
)
@click.option(
    "--workflow-id",
    default=None,
    help="Process a single specific workflow, bypassing eligibility checks",
)
def mine(
    ctx: click.Context,
    limit: Optional[int],
    dry_run: bool,
    workflow_id: Optional[str],
) -> None:
    """Run the offline learning pipeline over eligible workflows."""
    service = _resolve_service(ctx)
    from ace.cli.commands.mine import _handle_mine

    _handle_mine(service, limit=limit, dry_run=dry_run, workflow_id=workflow_id)


if __name__ == "__main__":
    run()
