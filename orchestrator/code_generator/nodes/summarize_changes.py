"""Node: summarize_changes — build the consumer-facing code_generation_summary.

Reads the deterministic ``raw_results`` (parsed by ``load_raw_results``) plus
the work plan, the actual ``git diff`` from the working directory, and the
optional pre/post-execution reasoning diary, and calls an LLM to author a
natural-language ``description`` describing what shipped. Merges the two
into ``code_generation_summary`` so downstream consumers
(``push_and_create_pr``, dispatcher CLI/TUI, ACE evaluator/reflector) see
the same shape they always did.

Design notes:

* **Separation of authority.** ``raw_results`` is the ground truth for
  ``status``, ``branch``, ``commit_sha``, ``files_changed``, ``build``,
  ``tests``, ``error`` — anything a machine can determine. The LLM only
  writes free-text. This kills the AOS-239 failure mode where a
  soft-contract LLM output silently truncated the workflow.
* **Best-effort.** LLM failures do not fail the workflow. On any error
  we fall back to a deterministic description synthesized from
  ``files_changed`` + work-plan tasks, so the PR/JIRA comment is still
  informative and the workflow proceeds to ``push_and_create_pr``.
* **Idempotent.** Safe to re-run on the same commit — output depends only
  on ``raw_results`` + ``git diff`` + the work plan.
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import cast

import litellm
from litellm import ModelResponse

from orchestrator.code_generator.state import (
    SummarizeChangesInputState,
    SummarizeChangesOutputState,
)
from orchestrator.utils import litellm_call_kwargs

logger = logging.getLogger(__name__)

_MAX_DIFF_CHARS = 40_000
_MAX_REASONING_CHARS = 8_000

_SYSTEM = """\
You are describing what a coding agent actually shipped in a pull request.

You will be given:
- the approved work plan (goal, approach, tasks),
- structured results from the run (status, files changed, build/tests),
- the actual git diff of the branch against main,
- optionally, the agent's own reasoning diary.

Write a factual, neutral description of what changed based ONLY on the diff
and structured results — not what the agent said it did. Be specific: name
the files/functions touched and what changed about them. Do not speculate
about intent beyond what the diff shows.

Respond as plain markdown, no fenced code blocks around the whole response,
2–6 short paragraphs (or bulleted sections). Do not restate the work-plan
verbatim. Do not include a header — the caller adds one. Do not include a
build/test/status summary — that is rendered separately from the structured
fields.
"""

_HUMAN_TEMPLATE = """\
## Ticket
{ticket_key}

## Work plan
Summary: {summary}
Approach: {approach}

Tasks:
{tasks}

## Structured results
Status: {status}
Branch: {branch}
Commit: {commit_sha}
Build: {build}
Tests: {tests}
Files changed ({n_files}):
{files}

## Diff (against {diff_base})
```
{diff}
```

{reasoning_block}"""


def _git_diff(working_dir: str, base_sha: str) -> str:
    """Return the diff of HEAD against ``base_sha``, truncated to a sane size."""
    if not working_dir or not os.path.isdir(working_dir) or not base_sha:
        return ""
    try:
        proc = subprocess.run(
            ["git", "diff", f"{base_sha}..HEAD"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Failed to compute git diff for summariser: %s", exc)
        return ""
    if proc.returncode != 0:
        return ""
    diff = proc.stdout
    if len(diff) > _MAX_DIFF_CHARS:
        diff = diff[:_MAX_DIFF_CHARS] + "\n\n[... diff truncated ...]"
    return diff


def _read_reasoning(reasoning_path: str) -> str:
    if not reasoning_path or not os.path.isfile(reasoning_path):
        return ""
    try:
        with open(reasoning_path, "r") as f:
            text = f.read().strip()
    except OSError:
        return ""
    if len(text) > _MAX_REASONING_CHARS:
        text = text[:_MAX_REASONING_CHARS] + "\n[... truncated ...]"
    return text


def _fallback_description(
    work_plan: dict,
    raw_results: dict,
) -> str:
    """Deterministic description used when the LLM call fails or is unusable."""
    files = raw_results.get("files_changed") or []
    tasks = work_plan.get("tasks") or []
    lines = []
    summary = work_plan.get("summary") or ""
    if summary:
        lines.append(summary)
    if tasks:
        lines.append("")
        lines.append("**Tasks addressed:**")
        for task in tasks:
            if isinstance(task, dict):
                desc = task.get("description") or task.get("title") or ""
                if desc:
                    lines.append(f"- {desc}")
    if files:
        lines.append("")
        lines.append("**Files changed:**")
        for f in files:
            lines.append(f"- `{f}`")
    error = raw_results.get("error")
    if error:
        lines.append("")
        lines.append(f"**Error reported by recipe:** {error}")
    return "\n".join(lines).strip() or "No description available."


def _merge_summary(raw_results: dict, description: str) -> dict:
    """Merge raw_results + LLM description into the canonical summary shape."""
    merged = dict(raw_results)
    # Never leak internal fields to consumers.
    merged.pop("diff_base_sha", None)
    if description:
        merged["description"] = description
    return merged


def summarize_changes(state: SummarizeChangesInputState) -> SummarizeChangesOutputState:
    """Produce ``code_generation_summary`` from raw_results + git diff + LLM.

    Reads:  ticket_key, work_plan_data, raw_results, working_dir, reasoning_path
    Writes: code_generation_summary
    """
    ticket_key = state.get("ticket_key", "")
    work_plan = state.get("work_plan_data") or {}
    raw_results = state.get("raw_results") or {}
    working_dir = state.get("working_dir", "")
    reasoning_path = state.get("reasoning_path", "")

    if not raw_results:
        # load_raw_results already routed to persist_results via exec_error in
        # this case; if we somehow got here without raw_results, keep whatever
        # code_generation_summary is already in state.
        logger.warning("summarize_changes invoked with no raw_results — skipping LLM call")
        existing_summary = state.get("code_generation_summary") if isinstance(state, dict) else None
        return {
            "code_generation_summary": (
                existing_summary if isinstance(existing_summary, dict) else {}
            )
        }

    status = raw_results.get("status", "unknown")

    # Skip the LLM call on hard-failed runs — the fallback description is
    # more useful than paraphrasing "no commit was produced", and we avoid
    # wasting an LLM call on the failure path.
    if status == "failed" or not raw_results.get("commit_sha"):
        description = _fallback_description(work_plan, raw_results)
        return {"code_generation_summary": _merge_summary(raw_results, description)}

    diff = _git_diff(working_dir, raw_results.get("diff_base_sha", ""))
    reasoning = _read_reasoning(reasoning_path)

    tasks = work_plan.get("tasks") or []
    task_lines = (
        "\n".join(
            f"- {t.get('description', t.get('title', ''))}" for t in tasks if isinstance(t, dict)
        )
        or "(none)"
    )

    files = raw_results.get("files_changed") or []
    files_block = "\n".join(f"- {f}" for f in files) or "(none)"

    reasoning_block = f"## Agent reasoning diary\n```\n{reasoning}\n```" if reasoning else ""

    human = _HUMAN_TEMPLATE.format(
        ticket_key=ticket_key,
        summary=work_plan.get("summary", ""),
        approach=work_plan.get("approach", ""),
        tasks=task_lines,
        status=status,
        branch=raw_results.get("branch", ""),
        commit_sha=raw_results.get("commit_sha", ""),
        build=raw_results.get("build", ""),
        tests=raw_results.get("tests", ""),
        n_files=len(files),
        files=files_block,
        diff_base=raw_results.get("diff_base_sha", "") or "main",
        diff=diff or "(diff unavailable)",
        reasoning_block=reasoning_block,
    )

    model = os.environ.get("SUMMARIZER_MODEL") or os.environ.get("GOOSE_MODEL", "")
    if not model:
        logger.warning("SUMMARIZER_MODEL/GOOSE_MODEL not set — using fallback description")
        description = _fallback_description(work_plan, raw_results)
        return {"code_generation_summary": _merge_summary(raw_results, description)}

    try:
        kwargs = litellm_call_kwargs(model)
        raw_response = litellm.completion(
            **kwargs,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": human},
            ],
        )
        if not hasattr(raw_response, "choices"):
            raise TypeError(f"Unexpected litellm response type: {type(raw_response)}")
        response = cast(ModelResponse, raw_response)
        description = (response.choices[0].message.content or "").strip()
        if not description:
            raise ValueError("LLM returned empty description")
        logger.info("summarize_changes produced %d-char description", len(description))
    except Exception as exc:  # noqa: BLE001
        logger.warning("summarize_changes LLM call failed, using fallback: %s", exc)
        description = _fallback_description(work_plan, raw_results)

    return {"code_generation_summary": _merge_summary(raw_results, description)}
