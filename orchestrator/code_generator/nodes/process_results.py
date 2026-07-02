"""Node: process_results — parse the code generation summary JSON written by the recipe.

This node is stateless and pure with respect to I/O: it only reads a file path
from state and returns the parsed dict.  It is fully unit-testable by injecting
a real temp file — no subprocess or database required.
"""

import json

import click

from orchestrator.code_generator.state import ProcessResultsInputState, ProcessResultsOutputState
from orchestrator.shared.repo_setup.nodes.common import code_generation_failure_summary


def process_results(state: ProcessResultsInputState) -> ProcessResultsOutputState:
    """Read and parse the code generation summary JSON written by the Goose recipe.

    Reads:  summary_path, ticket_key
    Writes: code_generation_summary
    """
    ticket_key = state.get("ticket_key", "")
    summary_path = state.get("summary_path", "")

    try:
        with open(summary_path, "r") as f:
            code_generation_summary = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        click.echo(f"⚠️  Could not read code generation summary: {exc}", err=True)
        code_generation_summary = code_generation_failure_summary(
            ticket_key,
            f"Code generation summary not written by recipe: {exc}",
        )

    return {"code_generation_summary": code_generation_summary}
