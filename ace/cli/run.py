"""
ACE CLI entrypoint.

Thin scaffold mirroring ``dispatcher/run.py``. Subcommands (``mine``,
``items``, ``promote``, ``reject``, ``stats``, ``ontology``) will be wired
up as the mining/review pipeline lands.
"""

import click


@click.command()
def run() -> None:
    """Entry point for the ``ace`` CLI; subcommands not yet implemented."""
    click.echo("ace: no commands implemented yet")


if __name__ == "__main__":
    run()
