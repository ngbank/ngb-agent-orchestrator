"""Node: infer_branch_prefix — classify work plan into a git branch namespace."""

import json
import os

import click
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_litellm import ChatLiteLLM

from orchestrator.code_generator.state import CodeGeneratorState

_VALID_PREFIXES = {"feature", "bugfix", "chore", "docs"}
_FALLBACK = "feature"

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
    Writes: branch_prefix
    Falls back to 'feature' on any error or unexpected response.
    """
    work_plan = state.get("work_plan_data") or {}
    summary = work_plan.get("summary", "")
    approach = work_plan.get("approach", "")
    tasks = work_plan.get("tasks", [])

    task_lines = "\n".join(f"- {t.get('description', t.get('title', ''))}" for t in tasks)

    model = os.environ.get("GOOSE_MODEL", "")
    if not model:
        click.echo("⚠️  GOOSE_MODEL not set — defaulting branch prefix to 'feature'")
        return {"branch_prefix": _FALLBACK}

    try:
        llm = ChatLiteLLM(model=model, max_tokens=64, temperature=0)
        response = llm.invoke(
            [
                SystemMessage(content=_SYSTEM),
                HumanMessage(
                    content=_HUMAN_TEMPLATE.format(
                        summary=summary, approach=approach, tasks=task_lines
                    )
                ),
            ]
        )
        content = response.content
        raw = (content if isinstance(content, str) else str(content)).strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        data = json.loads(raw)
        prefix = data.get("prefix", "").lower().strip()
        if prefix not in _VALID_PREFIXES:
            click.echo(f"⚠️  LLM returned unexpected prefix '{prefix}' — defaulting to 'feature'")
            return {"branch_prefix": _FALLBACK}
        click.echo(f"🌿 Branch prefix inferred: {prefix}")
        return {"branch_prefix": prefix}
    except Exception as exc:  # noqa: BLE001
        click.echo(f"⚠️  Branch prefix inference failed ({exc}) — defaulting to 'feature'")
        return {"branch_prefix": _FALLBACK}
