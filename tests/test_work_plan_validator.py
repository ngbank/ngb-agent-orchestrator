"""
Tests for dispatcher.work_plan_validator
"""

import pytest

from dispatcher.work_plan_validator import (
    WorkPlan,
    WorkPlanValidationError,
    validate_work_plan,
)


def _valid_work_plan(**overrides) -> dict:
    base = {
        "schema_version": "1.0",
        "ticket_key": "MINIBANK-123",
        "summary": "Add login endpoint",
        "approach": "Implement JWT-based login in the auth service.",
        "tasks": [
            {
                "id": 1,
                "description": "Create login handler",
                "files_likely_affected": ["auth/handler.py"],
            }
        ],
        "risks": ["Session token expiry edge cases"],
        "questions_for_reviewer": ["Should we support refresh tokens?"],
        "status": "pass",
    }
    base.update(overrides)
    return base


class TestValidWorkPlan:
    def test_returns_work_plan_dataclass(self):
        result = validate_work_plan(_valid_work_plan())
        assert isinstance(result, WorkPlan)
        assert result.ticket_key == "MINIBANK-123"
        assert result.schema_version == "1.0"
        assert result.status == "pass"

    def test_parses_tasks(self):
        result = validate_work_plan(_valid_work_plan())
        assert len(result.tasks) == 1
        assert result.tasks[0].id == 1
        assert result.tasks[0].description == "Create login handler"
        assert result.tasks[0].files_likely_affected == ["auth/handler.py"]

    def test_concerns_status_is_valid(self):
        result = validate_work_plan(_valid_work_plan(status="concerns"))
        assert result.status == "concerns"

    def test_blocked_status_is_valid(self):
        result = validate_work_plan(_valid_work_plan(status="blocked"))
        assert result.status == "blocked"

    def test_empty_risks_and_questions_allowed(self):
        result = validate_work_plan(_valid_work_plan(risks=[], questions_for_reviewer=[]))
        assert result.risks == []
        assert result.questions_for_reviewer == []


class TestMissingRequiredFields:
    @pytest.mark.parametrize(
        "field",
        [
            "schema_version",
            "ticket_key",
            "summary",
            "approach",
            "tasks",
            "risks",
            "questions_for_reviewer",
            "status",
        ],
    )
    def test_missing_field_raises_error(self, field):
        data = _valid_work_plan()
        del data[field]
        with pytest.raises(WorkPlanValidationError, match="WorkPlan validation failed"):
            validate_work_plan(data)


class TestSchemaVersion:
    def test_invalid_schema_version_raises_error(self):
        with pytest.raises(WorkPlanValidationError):
            validate_work_plan(_valid_work_plan(schema_version="2.0"))

    def test_wrong_type_schema_version_raises_error(self):
        with pytest.raises(WorkPlanValidationError):
            validate_work_plan(_valid_work_plan(schema_version=1))


class TestStatusField:
    def test_invalid_status_raises_error(self):
        with pytest.raises(WorkPlanValidationError, match="WorkPlan validation failed"):
            validate_work_plan(_valid_work_plan(status="unknown"))

    def test_uppercase_status_raises_error(self):
        with pytest.raises(WorkPlanValidationError):
            validate_work_plan(_valid_work_plan(status="Pass"))


class TestTasksField:
    def test_non_list_tasks_raises_error(self):
        with pytest.raises(WorkPlanValidationError):
            validate_work_plan(_valid_work_plan(tasks="do it all"))

    def test_empty_tasks_raises_error(self):
        with pytest.raises(WorkPlanValidationError):
            validate_work_plan(_valid_work_plan(tasks=[]))

    def test_task_missing_id_raises_error(self):
        bad_task = {"description": "Do something", "files_likely_affected": []}
        with pytest.raises(WorkPlanValidationError):
            validate_work_plan(_valid_work_plan(tasks=[bad_task]))

    def test_task_missing_description_raises_error(self):
        bad_task = {"id": 1, "files_likely_affected": []}
        with pytest.raises(WorkPlanValidationError):
            validate_work_plan(_valid_work_plan(tasks=[bad_task]))

    def test_task_missing_files_likely_affected_raises_error(self):
        bad_task = {"id": 1, "description": "Do something"}
        with pytest.raises(WorkPlanValidationError):
            validate_work_plan(_valid_work_plan(tasks=[bad_task]))


class TestAdditionalProperties:
    def test_extra_top_level_field_raises_error(self):
        data = _valid_work_plan()
        data["unexpected_field"] = "oops"
        with pytest.raises(WorkPlanValidationError):
            validate_work_plan(data)

    def test_extra_task_field_raises_error(self):
        data = _valid_work_plan()
        data["tasks"][0]["extra"] = "nope"
        with pytest.raises(WorkPlanValidationError):
            validate_work_plan(data)
