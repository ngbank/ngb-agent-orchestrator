"""Pure-function unit tests for graph/executor/nodes/process_results.py.

These tests require no subprocess, no database, and no Goose binary.
They only interact with the filesystem via real temp files.
"""

import json
import os
import tempfile


def test_process_results_parses_valid_summary_json():
    """process_results returns the parsed dict when the summary file is valid JSON."""
    from orchestrator.code_generator.nodes.process_results import process_results

    summary = {
        "ticket_key": "AOS-97",
        "branch": "feature/AOS-97+test",
        "build": "pass",
        "tests": "pass",
        "files_changed": ["foo.py"],
        "commit_sha": "abc123",
        "pr_url": "https://github.com/org/repo/pull/5",
        "status": "success",
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(summary, f)
        path = f.name

    try:
        result = process_results({"ticket_key": "AOS-97", "summary_path": path})
        assert result["execution_summary"] == summary
    finally:
        os.unlink(path)


def test_process_results_returns_failure_summary_on_missing_file():
    """process_results gracefully handles a missing summary file."""
    from orchestrator.code_generator.nodes.process_results import process_results

    result = process_results({"ticket_key": "AOS-97", "summary_path": "/nonexistent/path_xyz.json"})
    summary = result["execution_summary"]

    assert summary["status"] == "failed"
    assert summary["ticket_key"] == "AOS-97"
    assert "not written by recipe" in summary["error"]


def test_process_results_returns_failure_summary_on_invalid_json():
    """process_results gracefully handles a summary file with invalid JSON."""
    from orchestrator.code_generator.nodes.process_results import process_results

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write("{ not valid json {{")
        path = f.name

    try:
        result = process_results({"ticket_key": "AOS-97", "summary_path": path})
        summary = result["execution_summary"]
        assert summary["status"] == "failed"
        assert summary["ticket_key"] == "AOS-97"
    finally:
        os.unlink(path)


def test_process_results_partial_status_preserved():
    """process_results does not modify a 'partial' status returned by the recipe."""
    from orchestrator.code_generator.nodes.process_results import process_results

    summary = {
        "ticket_key": "AOS-97",
        "branch": "feature/AOS-97+test",
        "build": "pass",
        "tests": "fail",
        "files_changed": [],
        "commit_sha": "def456",
        "pr_url": "",
        "status": "partial",
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(summary, f)
        path = f.name

    try:
        result = process_results({"ticket_key": "AOS-97", "summary_path": path})
        assert result["execution_summary"]["status"] == "partial"
    finally:
        os.unlink(path)
