"""Unit tests for graph/work_planner/nodes/generate_plan.py."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from graph.work_planner.nodes.generate_plan import generate_plan

VALID_WORK_PLAN = {
    "schema_version": "1.0",
    "ticket_key": "AOS-51",
    "summary": "Implement plan generation",
    "approach": "Shell out to Goose plan recipe",
    "tasks": [{"id": 1, "description": "Do the thing", "files_likely_affected": ["a.py"]}],
    "risks": [],
    "questions_for_reviewer": [],
    "status": "pass",
}


def _make_run_result(returncode=0):
    mock = MagicMock()
    mock.returncode = returncode
    return mock


@pytest.fixture
def write_workplan_to_output(tmp_path):
    """Side effect factory: writes a WorkPlan JSON to the output_path arg."""

    def _side_effect(cmd, check):
        # Extract output_path from the --params output_path=<value> argument
        params = [a for a in cmd if a.startswith("output_path=")]
        assert params, "output_path param not passed to goose"
        output_path = params[0].split("=", 1)[1]
        with open(output_path, "w") as f:
            json.dump(VALID_WORK_PLAN, f)
        result = MagicMock()
        result.returncode = 0
        return result

    return _side_effect


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_generate_plan_success(write_workplan_to_output):
    """Goose writes a valid WorkPlan → work_plan_data returned in state."""
    with patch("graph.work_planner.nodes.generate_plan.subprocess.run") as mock_run:
        mock_run.side_effect = write_workplan_to_output
        result = generate_plan({"ticket_key": "AOS-51"})

    assert "work_plan_data" in result
    assert result["work_plan_data"]["ticket_key"] == "AOS-51"
    assert "error" not in result


def test_generate_plan_passes_correct_params(write_workplan_to_output):
    """Goose is invoked with the correct recipe and params."""
    with patch("graph.work_planner.nodes.generate_plan.subprocess.run") as mock_run:
        mock_run.side_effect = write_workplan_to_output
        generate_plan({"ticket_key": "AOS-51"})

    cmd = mock_run.call_args[0][0]
    assert "goose" in cmd
    assert "recipes/plan.yaml" in cmd
    assert any(a == "ticket_key=AOS-51" for a in cmd)
    assert any(a.startswith("output_path=") for a in cmd)


def test_generate_plan_cleans_up_temp_file(write_workplan_to_output):
    """Temp output file is deleted after successful run."""
    captured_path = {}

    def _side_effect(cmd, check):
        params = [a for a in cmd if a.startswith("output_path=")]
        output_path = params[0].split("=", 1)[1]
        captured_path["path"] = output_path
        with open(output_path, "w") as f:
            json.dump(VALID_WORK_PLAN, f)
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("graph.work_planner.nodes.generate_plan.subprocess.run") as mock_run:
        mock_run.side_effect = _side_effect
        generate_plan({"ticket_key": "AOS-51"})

    assert not os.path.exists(captured_path["path"])


# ---------------------------------------------------------------------------
# Goose failure paths
# ---------------------------------------------------------------------------


def test_generate_plan_goose_nonzero_exit():
    """Goose exits non-zero → error returned, no work_plan_data."""
    with patch("graph.work_planner.nodes.generate_plan.subprocess.run") as mock_run:
        mock_run.return_value = _make_run_result(returncode=1)
        result = generate_plan({"ticket_key": "AOS-51"})

    assert "error" in result
    assert "1" in result["error"]
    assert "work_plan_data" not in result


def test_generate_plan_output_file_missing():
    """Goose exits 0 but writes no file → error returned."""
    with patch("graph.work_planner.nodes.generate_plan.subprocess.run") as mock_run:
        mock_run.return_value = _make_run_result(returncode=0)
        # Don't write anything to output_path — file stays empty from mkstemp
        # but we simulate Goose deleting it
        with patch("builtins.open", side_effect=FileNotFoundError):
            result = generate_plan({"ticket_key": "AOS-51"})

    assert "error" in result
    assert "did not write" in result["error"]
    assert "work_plan_data" not in result


def test_generate_plan_invalid_json():
    """Goose writes a file that isn't valid JSON → error returned."""

    def _write_bad_json(cmd, check):
        params = [a for a in cmd if a.startswith("output_path=")]
        output_path = params[0].split("=", 1)[1]
        with open(output_path, "w") as f:
            f.write("this is not json {{")
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("graph.work_planner.nodes.generate_plan.subprocess.run") as mock_run:
        mock_run.side_effect = _write_bad_json
        result = generate_plan({"ticket_key": "AOS-51"})

    assert "error" in result
    assert "invalid JSON" in result["error"]
    assert "work_plan_data" not in result


def test_generate_plan_empty_json():
    """Goose writes an empty JSON object → error returned."""

    def _write_empty(cmd, check):
        params = [a for a in cmd if a.startswith("output_path=")]
        output_path = params[0].split("=", 1)[1]
        with open(output_path, "w") as f:
            json.dump({}, f)
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("graph.work_planner.nodes.generate_plan.subprocess.run") as mock_run:
        mock_run.side_effect = _write_empty
        result = generate_plan({"ticket_key": "AOS-51"})

    assert "error" in result
    assert "empty" in result["error"]
    assert "work_plan_data" not in result
