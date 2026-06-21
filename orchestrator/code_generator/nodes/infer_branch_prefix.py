"""Node: infer_branch_prefix — classify work plan into a git branch namespace."""

import json
import os
import re
from typing import cast

import click
import litellm
from litellm import ModelResponse

from orchestrator.code_generator.state import CodeGeneratorState
from orchestrator.utils import litellm_call_kwargs

_VALID_PREFIXES = {"feature", "bugfix", "chore", "docs"}

_SYSTEM = """\
You classify software work into exactly one git branch namespace.

Rules:
- feature  → new user-facing functionality or capabilities
- bugfix   → fixing incorrect behaviour, errors, or regressions
- chore    → maintenance: refactoring, CI, tooling, dependency updates, tests, config
- docs     → documentation only, no code logic changes

Respond with JSON only — no explanation, no markdown:
{"prefix": "<feature|bugfix|chore|docs>"}"""

_HUMAN_TEMPLATE = """\
Summary: {summary}

Approach: {approach}

Tasks:
{tasks}"""


def infer_branch_prefix(state: CodeGeneratorState) -> dict:
    """Call an LLM to classify the work plan into a branch namespace.

    Reads:  work_plan_data
    Writes: branch_prefix on success; exec_error + failed_node on failure.
    """
    work_plan = state.get("work_plan_data") or {}
    summary = work_plan.get("summary", "")
    approach = work_plan.get("approach", "")
    tasks = work_plan.get("tasks", [])

    task_lines = "\n".join(f"- {t.get('description', t.get('title', ''))}" for t in tasks)

    model = os.environ.get("GOOSE_MODEL", "")
    if not model:
        msg = "GOOSE_MODEL is not set — cannot infer branch prefix"
        click.echo(f"❌ {msg}", err=True)
        return {"exec_error": msg, "failed_node": "infer_branch_prefix"}

    try:
        kwargs = litellm_call_kwargs(model)
        raw_response = litellm.completion(
            **kwargs,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": _HUMAN_TEMPLATE.format(
                        summary=summary, approach=approach, tasks=task_lines
                    ),
                },
            ],
            max_tokens=64,
            temperature=0,
        )
        if not hasattr(raw_response, "choices"):
            raise TypeError(f"Unexpected litellm response type: {type(raw_response)}")
        response = cast(ModelResponse, raw_response)
        choice = response.choices[0]
        msg = choice.message
        # Temporary diagnostic: surface the full response structure
        click.echo(
            f"[infer_branch_prefix] finish_reason={choice.finish_reason!r} "
            f"content={msg.content!r} "
            f"tool_calls={getattr(msg, 'tool_calls', None)!r} "
            f"reasoning_content={getattr(msg, 'reasoning_content', None)!r}",
            err=True,
        )
        raw = (msg.content or "").strip()
        if not raw:
            raise ValueError("LLM returned empty content")
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        # Fallback: extract the first {...} block when the model returns free-form text
        if not raw.startswith("{"):
            m = re.search(r"\{[^}]+\}", raw)
            if m:
                raw = m.group()
        data = json.loads(raw)
        prefix = data.get("prefix", "").lower().strip()
        if prefix not in _VALID_PREFIXES:
            msg = (
                f"LLM returned unrecognised branch prefix '{prefix}'"
                f" — expected one of {sorted(_VALID_PREFIXES)}"
            )
            click.echo(f"❌ {msg}", err=True)
            return {"exec_error": msg, "failed_node": "infer_branch_prefix"}
        click.echo(f"🌿 Branch prefix inferred: {prefix}")
        return {"branch_prefix": prefix}
    except Exception as exc:  # noqa: BLE001
        msg = f"Branch prefix inference failed: {exc}"
        click.echo(f"❌ {msg}", err=True)
        return {"exec_error": msg, "failed_node": "infer_branch_prefix"}
