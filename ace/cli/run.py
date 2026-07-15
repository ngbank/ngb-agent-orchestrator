"""
ACE CLI entrypoint.

Thin scaffold mirroring ``dispatcher/run.py``. Subcommands (``mine``,
``items``, ``promote``, ``reject``, ``stats``, ``ontology``) are wired
up as the mining/review pipeline lands.
"""

import click

run = click.Group()


@click.command(name="stats")
def stats_cmd() -> None:
    """Print read-only mining summary from ACE tables."""
    from ace.cli.commands.stats import handle_stats

    handle_stats()


run.add_command(stats_cmd)
