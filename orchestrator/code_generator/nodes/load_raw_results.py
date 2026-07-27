"""Node: load_raw_results — parse raw_results.json written by the recipe finalizer.

Replaces the old ``process_results`` node. Two contract changes:

* **Different output field.** Populates ``raw_results`` (dict). The consumer-
  facing ``code_generation_summary`` is built by the downstream
  ``summarize_changes`` node from ``raw_results`` + an LLM description.
* **Fail loud on missing/malformed file.** The old node substituted a
  failure summary and let the pipeline continue to ``push_and_create_pr``
  (which then silently skipped because ``branch``/``commit_sha`` were
  empty). That masked "recipe didn't finish" as "no changes to push" and
  is exactly how AOS-239 lost 13 minutes of code-gen work with
  ``retry_count = 0``. Here we surface it as an ``exec_error`` + failure
  summary + ``failed_node`` so the workflow is properly marked FAILED and
  the operator can retry.

The file at ``raw_results_path`` is emitted deterministically by
``orchestrator/code_generator/scripts/write_raw_results.py`` as the final
shell step of the generate_code recipe, so a missing file means the recipe
did not run to completion.
"""

import json

import click

from orchestrator.code_generator.state import (
    LoadRawResultsInputState,
    LoadRawResultsOutputState,
)
from orchestrator.shared.repo_setup.nodes.common import code_generation_failure_summary


def load_raw_results(state: LoadRawResultsInputState) -> LoadRawResultsOutputState:
    """Load and parse ``raw_results.json`` written by the recipe finalizer.

    Reads:  raw_results_path, ticket_key
    Writes on success: raw_results
    Writes on failure: raw_results (empty dict), code_generation_summary
                      (failure shape), exec_error, failed_node
    """
    ticket_key = state.get("ticket_key", "")
    raw_results_path = state.get("raw_results_path", "")

    try:
        with open(raw_results_path, "r") as f:
            raw_results = json.load(f)
        if not isinstance(raw_results, dict):
            raise TypeError(f"raw_results.json is not a JSON object: got {type(raw_results)}")
    except (FileNotFoundError, json.JSONDecodeError, TypeError, OSError) as exc:
        message = f"raw_results.json not written by recipe finalizer: {exc}"
        click.echo(f"❌ {message}", err=True)
        return {
            "raw_results": {},
            "code_generation_summary": code_generation_failure_summary(ticket_key, message),
            "exec_error": message,
            "failed_node": "generate_code",
        }

    return {"raw_results": raw_results}
