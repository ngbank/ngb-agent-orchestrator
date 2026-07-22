"""Node: run_goose — invoke the Goose generate recipe against the cloned workspace."""

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from ace.config import get_ace_settings
from orchestrator.code_generator.state import RunGooseInputState
from orchestrator.utils import goose_session, run_and_tee

logger = logging.getLogger(__name__)


def _get_developer_rules() -> list[dict[str, str]]:
    """Load developer rules only when this node prepares recipe inputs."""
    from mcp_server.server import get_developer_rules

    return get_developer_rules()


def _project_from_ticket_key(ticket_key: str) -> str:
    """Return the JIRA project short-name from a ticket key."""
    return ticket_key.split("-", 1)[0] if "-" in ticket_key else ticket_key


def _write_temp_file(ticket_key: str, suffix: str, content: str) -> str:
    """Write recipe input content to a temporary file and return its path."""
    fd, path = tempfile.mkstemp(suffix=suffix, prefix=f"{ticket_key}_")
    os.close(fd)
    with open(path, "w") as file:
        file.write(content)
    return path


def _render_context_block(
    ticket_key: str,
    summary: str,
    query_text: str,
    top_k: int,
) -> str:
    """Load ACE retrieval dependencies only when context injection is active."""
    from ace.retrieval import render_context_block
    from ace.retrieval.synthesizer import TicketContext

    return render_context_block(
        TicketContext(
            ticket_key=ticket_key,
            ticket_summary=summary,
            project=_project_from_ticket_key(ticket_key),
            recipe_target="code_generator",
        ),
        query_text=query_text,
        top_k=top_k,
    )


def _write_context_items_file(
    ticket_key: str,
    work_plan: dict[str, Any],
    pr_comments: str,
) -> str | None:
    """Retrieve and render ACE context for code generation into a temporary file."""
    settings = get_ace_settings()
    is_pr_rerun = bool(pr_comments)
    if not (settings.is_code_generator_active() or (is_pr_rerun and settings.is_pr_rerun_active())):
        return None

    try:
        summary = work_plan.get("summary", "")
        tasks = work_plan.get("tasks", [])
        task_details = " ".join(
            " ".join(
                filter(
                    None,
                    (task.get("description", ""), *task.get("files_likely_affected", [])),
                )
            )
            for task in tasks
            if isinstance(task, dict)
        )
        query_text = " ".join(filter(None, (summary, task_details, pr_comments)))
        block = _render_context_block(
            ticket_key,
            summary,
            query_text,
            settings.top_k,
        )
    except Exception:  # noqa: BLE001 -- ACE retrieval must not block generation
        logger.warning(
            "ACE context retrieval failed for %s -- proceeding without context items",
            ticket_key,
            exc_info=True,
        )
        return None

    return _write_temp_file(ticket_key, "_context_items.md", block) if block.strip() else None


def run_goose(state: RunGooseInputState) -> dict:
    """Shell out to `goose run --recipe orchestrator/code_generator/recipes/generate_code.yaml`.

    goose_session is opened and closed entirely within this node — it is the
    only node that requires a live Goose session.

    Reads:  workflow_id, ticket_key, working_dir, work_plan_path, summary_path,
            reasoning_path, code_generation_summary (for existing_branch on PR re-runs),
            pr_comments
    Writes: nothing (summary written to summary_path on disk by the recipe)
    """
    workflow_id = state.get("workflow_id")
    ticket_key = state.get("ticket_key", "")
    working_dir = state.get("working_dir", "")
    work_plan_path = state.get("work_plan_path", "")
    summary_path = state.get("summary_path", "")
    reasoning_path = state.get("reasoning_path", "")

    # Existing branch is used on PR re-runs to avoid re-creating the branch.
    existing_exec_summary = state.get("code_generation_summary") or {}
    existing_branch = existing_exec_summary.get("branch", "")
    pr_comments = state.get("pr_comments") or ""

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
    developer_rules_path = _write_temp_file(
        ticket_key,
        "_developer_rules.json",
        json.dumps(_get_developer_rules(), indent=2),
    )
    pr_comments_path = (
        _write_temp_file(ticket_key, "_pr_comments.md", pr_comments) if pr_comments else None
    )
    context_items_path = _write_context_items_file(ticket_key, _work_plan, pr_comments)

    mcp_python = os.environ.get("GOOSE_MCP_PYTHON", "python")
    max_turns = os.environ.get("GOOSE_MAX_TURNS", "200")
    recipe_path = Path(__file__).resolve().parents[1] / "recipes" / "generate_code.yaml"

    logger.info("Running generate recipe for %s...", ticket_key)

    command = [
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
        f"developer_rules_path={developer_rules_path}",
        "--params",
        f"branch_name={branch_name}",
    ]
    if pr_comments_path:
        command.extend(["--params", f"pr_comments_path={pr_comments_path}"])
    if context_items_path:
        command.extend(["--params", f"context_items_path={context_items_path}"])

    try:
        logger.info("=== goose run generate recipe ===")
        with goose_session(
            workflow_id=workflow_id, stage="generate_code", ticket_key=ticket_key
        ) as goose_env:
            result = run_and_tee(
                command,
                "subprocess.goose",
                cwd=working_dir,
                env=goose_env,
            )
    finally:
        for path in (developer_rules_path, pr_comments_path, context_items_path):
            if path and os.path.exists(path):
                os.unlink(path)

    # Append reasoning diary to workflow log.
    if os.path.exists(reasoning_path):
        reasoning_text = open(reasoning_path).read().strip()
        if reasoning_text:
            logger.info("\n%s\n  AGENT REASONING DIARY\n%s\n%s", "=" * 60, "=" * 60, reasoning_text)

    if result.returncode != 0:
        logger.warning("Goose exited with code %s", result.returncode)

    return {}
