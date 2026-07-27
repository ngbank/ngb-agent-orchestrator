"""Pure-function unit tests for ``orchestrator/code_generator/nodes/load_raw_results.py``.

The old ``process_results`` node silently substituted a failure summary when
the recipe's summary file was missing and let the pipeline continue to
``push_and_create_pr``. That masked AOS-239's "recipe ended without writing
the file" as "recipe produced no changes to push" and left ``retry_count = 0``
on a workflow that clearly should have been retried.

``load_raw_results`` replaces that with fail-loud semantics: on a missing or
malformed ``raw_results.json`` it sets ``exec_error`` so ``edges.route_after_
load_raw_results`` routes straight to ``persist_results`` and the workflow is
marked FAILED — which is what the retry surface acts on.
"""

import json
import os
import tempfile


def test_load_raw_results_returns_parsed_dict_on_valid_json():
    from orchestrator.code_generator.nodes.load_raw_results import load_raw_results

    raw = {
        "ticket_key": "AOS-291",
        "branch": "feature/AOS-291+test",
        "commit_sha": "abc123",
        "files_changed": ["foo.py", "bar.py"],
        "build": "pass",
        "tests": "pass",
        "status": "success",
        "diff_base_sha": "deadbeef",
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(raw, f)
        path = f.name

    try:
        result = load_raw_results({"ticket_key": "AOS-291", "raw_results_path": path})
        assert result["raw_results"] == raw
        # Happy path: no failure fields set.
        assert "exec_error" not in result
        assert "failed_node" not in result
        assert "code_generation_summary" not in result
    finally:
        os.unlink(path)


def test_load_raw_results_fails_loud_on_missing_file():
    """Missing raw_results.json → exec_error + failure summary + failed_node.

    This is the AOS-239 regression: previously the pipeline continued with an
    empty branch/commit and quietly gave up in push_and_create_pr, hiding the
    fact that the recipe never finished. The workflow must be marked FAILED
    so the operator can retry.
    """
    from orchestrator.code_generator.nodes.load_raw_results import load_raw_results

    result = load_raw_results(
        {"ticket_key": "AOS-291", "raw_results_path": "/nonexistent/path.json"}
    )

    assert result["exec_error"], "must surface a non-empty exec_error"
    assert result["failed_node"] == "generate_code"
    summary = result["code_generation_summary"]
    assert summary["status"] == "failed"
    assert summary["ticket_key"] == "AOS-291"
    assert "not written by recipe finalizer" in summary["error"]
    # raw_results is still populated (empty dict) so downstream code can
    # `.get()` safely without KeyError.
    assert result["raw_results"] == {}


def test_load_raw_results_fails_loud_on_invalid_json():
    from orchestrator.code_generator.nodes.load_raw_results import load_raw_results

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write("{ not valid json {{")
        path = f.name

    try:
        result = load_raw_results({"ticket_key": "AOS-291", "raw_results_path": path})
        assert result["exec_error"]
        assert result["failed_node"] == "generate_code"
        assert result["code_generation_summary"]["status"] == "failed"
    finally:
        os.unlink(path)


def test_load_raw_results_fails_loud_when_json_is_not_object():
    """A JSON array/scalar at the top level is not a valid raw_results shape."""
    from orchestrator.code_generator.nodes.load_raw_results import load_raw_results

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(["not", "a", "dict"], f)
        path = f.name

    try:
        result = load_raw_results({"ticket_key": "AOS-291", "raw_results_path": path})
        assert result["exec_error"]
        assert result["failed_node"] == "generate_code"
    finally:
        os.unlink(path)


def test_load_raw_results_preserves_partial_status():
    from orchestrator.code_generator.nodes.load_raw_results import load_raw_results

    raw = {
        "ticket_key": "AOS-291",
        "branch": "feature/AOS-291+test",
        "commit_sha": "def456",
        "files_changed": [],
        "build": "pass",
        "tests": "fail",
        "status": "partial",
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(raw, f)
        path = f.name

    try:
        result = load_raw_results({"ticket_key": "AOS-291", "raw_results_path": path})
        assert result["raw_results"]["status"] == "partial"
        assert "exec_error" not in result
    finally:
        os.unlink(path)
