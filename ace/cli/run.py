"""
ACE CLI entrypoint.

Thin scaffold mirroring ``dispatcher/run.py``. Subcommands (``mine``,
``items``, ``promote``, ``reject``, ``stats``, ``ontology``) are wired up
starting in Epic 3, ticket 3.1.
"""

import click


@click.command()
def run() -> None:
    """Entry point for the ``ace`` CLI; subcommands land in Epic 3."""
    click.echo("ace: no commands implemented yet")


if __name__ == "__main__":
    run()
