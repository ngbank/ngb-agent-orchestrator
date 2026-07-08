"""Static-integrity checks for ``orchestrator/work_planner/recipes/plan.yaml``.

The Goose plan recipe embeds Python code that runs in a subprocess to
validate the generated ``WorkPlan``.  Those embedded references drift
silently when the validator module moves — the recipe is not covered by
Python's import machinery, so a wrong module path only surfaces at
runtime as an infinite ``on_failure`` retry loop that eats the entire
``--max-turns`` budget.

These tests are a cheap guard: they load the recipe, verify no reference
to the *removed* ``dispatcher/work_plan_validator`` module remains, and
confirm the *current* import target is resolvable in the running Python
environment.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import yaml  # type: ignore[import-untyped]

RECIPE_PATH = (
    Path(__file__).resolve().parent.parent
    / "orchestrator"
    / "work_planner"
    / "recipes"
    / "plan.yaml"
)


def test_recipe_yaml_parses() -> None:
    """The recipe must be valid YAML — a syntax error would break the
    plan node in production and this is the cheapest thing to check."""
    with RECIPE_PATH.open() as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), "recipe root must be a mapping"


def test_recipe_does_not_reference_removed_dispatcher_validator() -> None:
    """``dispatcher/work_plan_validator.py`` was removed on 2026-06-23 in
    commit 7360ae0; the recipe must not resurrect it.

    A stale reference used to make every plan run fail its retry.checks
    subprocess with ``ModuleNotFoundError`` and enter an
    ``on_failure``-driven retry loop up to ``--max-turns 200``.
    """
    text = RECIPE_PATH.read_text()
    assert "from work_plan_validator" not in text, (
        "plan.yaml still imports from the removed dispatcher/work_plan_validator "
        "module. Replace with 'from orchestrator.work_planner.utilities import "
        "validate_work_plan'."
    )
    assert 'sys.path.insert(0, "dispatcher")' not in text
    assert "sys.path.insert(0, 'dispatcher')" not in text


def test_recipe_uses_importable_validator_target() -> None:
    """Whatever import path the recipe currently references must resolve
    in the Python environment the container will run — otherwise the
    retry.checks subprocess fails on every workflow.
    """
    from orchestrator.work_planner.utilities import validate_work_plan

    assert callable(validate_work_plan)

    # Sanity: the recipe should mention this exact import so the check
    # above is meaningful. If the recipe migrates to a different (still
    # importable) path in future, update this assertion accordingly.
    text = RECIPE_PATH.read_text()
    assert "from orchestrator.work_planner.utilities import validate_work_plan" in text, (
        "plan.yaml no longer imports validate_work_plan from "
        "orchestrator.work_planner.utilities — update this test if the "
        "import path changed intentionally."
    )

    # Confirm the module reference in the retry.checks shell command is
    # still importable (guards against renaming utilities/ without also
    # updating the recipe).
    importlib.import_module("orchestrator.work_planner.utilities")
