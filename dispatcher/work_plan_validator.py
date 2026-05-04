"""
WorkPlan Validator

Validates planner output against the WorkPlan JSON Schema contract
(schemas/work_plan_v1.json) before the dispatcher proceeds with execution.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema import ValidationError as JsonSchemaValidationError

_SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "work_plan_v1.json"


class WorkPlanValidationError(Exception):
    """Raised when planner output fails WorkPlan schema validation."""


@dataclass
class WorkPlanTask:
    id: int
    description: str
    files_likely_affected: list[str]


@dataclass
class WorkPlan:
    schema_version: str
    ticket_key: str
    summary: str
    approach: str
    tasks: list[WorkPlanTask]
    risks: list[str]
    questions_for_reviewer: list[str]
    status: str


def _load_schema() -> dict[str, Any]:
    try:
        with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise WorkPlanValidationError(f"WorkPlan schema file not found at {_SCHEMA_PATH}")
    except json.JSONDecodeError as e:
        raise WorkPlanValidationError(f"WorkPlan schema file is not valid JSON: {e}")


def validate_work_plan(data: dict[str, Any]) -> WorkPlan:
    """
    Validate raw planner output against the WorkPlan JSON Schema.

    Args:
        data: Dictionary representing the planner's WorkPlan output.

    Returns:
        A parsed WorkPlan dataclass if validation succeeds.

    Raises:
        WorkPlanValidationError: If the data does not conform to the schema,
            with a message describing the first validation failure.
    """
    schema = _load_schema()

    try:
        jsonschema.validate(instance=data, schema=schema)
    except JsonSchemaValidationError as e:
        raise WorkPlanValidationError(
            f"WorkPlan validation failed: {e.message} (path: {list(e.absolute_path)})"
        ) from e

    return WorkPlan(
        schema_version=data["schema_version"],
        ticket_key=data["ticket_key"],
        summary=data["summary"],
        approach=data["approach"],
        tasks=[
            WorkPlanTask(
                id=t["id"],
                description=t["description"],
                files_likely_affected=t["files_likely_affected"],
            )
            for t in data["tasks"]
        ],
        risks=data["risks"],
        questions_for_reviewer=data["questions_for_reviewer"],
        status=data["status"],
    )
