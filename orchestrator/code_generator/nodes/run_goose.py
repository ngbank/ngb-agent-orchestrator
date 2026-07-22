"""Node: run_goose — invoke the Goose generate recipe against the cloned workspace."""

import json
import logging
import os
import re
from pathlib import Path

from ace.config import get_ace_settings
from orchestrator.code_generator.state import RunGooseInputState
from orchestrator.context_items import retrieve_context_items, write_context_items_file
from orchestrator.utils import goose_session, run_and_tee

logger = logging.getLogger(__name__)


def _write_context_items_file(
    ticket_key: str,
    work_plan_data: dict,
    pr_comments: str,
) -> str | None:
    """Retrieve applicable context items and materialize them for the recipe."""
    settings = get_ace_settings()
    if not (settings.is_code_generator_active() or (pr_comments and settings.is_pr_rerun_active())):
        return None

    tasks = work_plan_data.get("tasks") or []
    task_descriptions = [task.get("description", "") for task in tasks if isinstance(task, dict)]
    affected_files = [
        file_path
        for task in tasks
        if isinstance(task, dict)
        for file_path in task.get("files_likely_affected", [])
    ]
    query_parts = [
        work_plan_data.get("summary", ""),
        *task_descriptions,
        *affected_files,
        pr_comments,
    ]
    block = retrieve_context_items(
        ticket_key=ticket_key,
        ticket_summary=work_plan_data.get("summary", ""),
        recipe_target="code_generator",
        query_text=" ".join(part for part in query_parts if part),
        top_k=settings.top_k,
    )
    return write_context_items_file(ticket_key, block)


def run_goose(state: RunGooseInputState) -> dict:
    """Shell out to `goose run --recipe orchestrator/code_generator/recipes/generate_code.yaml`.

    goose_session is opened and closed entirely within this node — it is the
    only node that requires a live Goose session.

    Reads:  workflow_id, ticket_key, working_dir, work_plan_path, summary_path,
            reasoning_path, pr_comments_path, pr_comments,
            code_generation_summary (for existing_branch on PR re-runs)
    Writes: nothing (summary written to summary_path on disk by the recipe)
    """
    workflow_id = state.get("workflow_id")
    ticket_key = state.get("ticket_key", "")
    working_dir = state.get("working_dir", "")
    work_plan_path = state.get("work_plan_path", "")
    summary_path = state.get("summary_path", "")
    reasoning_path = state.get("reasoning_path", "")
    pr_comments_path = state.get("pr_comments_path", "")
    pr_comments = state.get("pr_comments") or ""

    # Existing branch is used on PR re-runs to avoid re-creating the branch.
    existing_exec_summary = state.get("code_generation_summary") or {}
    existing_branch = existing_exec_summary.get("branch", "")

    branch_prefix = state.get("branch_prefix") or "feature"

    # Compute a deterministic branch name from the work plan summary + workflow_id suffix.
    # This prevents remote collisions when the same ticket is run multiple times.
    with open(work_plan_path) as _f:
        _work_plan = json.load(_f)
    _slug = (
        re.sub(r"[^a-z0-9]+", "-", _work_plan.get("summary", "").lower())
        .strip("-")[:40]
        .rstrip("-")
    )
    branch_name = f"{branch_prefix}/{ticket_key}+{_slug}-{str(workflow_id)[:8]}"
    context_items_path = _write_context_items_file(ticket_key, _work_plan, pr_comments)

    mcp_python = os.environ.get("GOOSE_MCP_PYTHON", "python")
    max_turns = os.environ.get("GOOSE_MAX_TURNS", "200")
    recipe_path = Path(__file__).resolve().parents[1] / "recipes" / "generate_code.yaml"

    logger.info("Running generate recipe for %s...", ticket_key)

    logger.info("=== goose run generate recipe ===")
    try:
        with goose_session(
            workflow_id=workflow_id, stage="generate_code", ticket_key=ticket_key
        ) as goose_env:
            result = run_and_tee(
                [
                    "goose",
                    "run",
                    "--recipe",
                    str(recipe_path),
                    "--max-turns",
                    max_turns,
                    "--params",
                    f"ticket_key={ticket_key}",
                    "--params",
                    f"work_plan_path={work_plan_path}",
                    "--params",
                    f"working_dir={working_dir}",
                    "--params",
                    f"output_path={summary_path}",
                    "--params",
                    f"reasoning_path={reasoning_path}",
                    "--params",
                    f"GOOSE_MCP_PYTHON={mcp_python}",
                    "--params",
                    f"existing_branch={existing_branch}",
                    "--params",
                    f"pr_comments_path={pr_comments_path}",
                    "--params",
                    f"context_items_path={context_items_path or ''}",
                    "--params",
                    f"branch_name={branch_name}",
                ],
                "subprocess.goose",
                cwd=working_dir,
                env=goose_env,
            )
    finally:
        if context_items_path and os.path.exists(context_items_path):
            os.unlink(context_items_path)

    # Append reasoning diary to workflow log.
    if os.path.exists(reasoning_path):
        reasoning_text = open(reasoning_path).read().strip()
        if reasoning_text:
            logger.info("\n%s\n  AGENT REASONING DIARY\n%s\n%s", "=" * 60, "=" * 60, reasoning_text)

    if result.returncode != 0:
        logger.warning("Goose exited with code %s", result.returncode)

    return {}
