"""Unit tests for orchestrator/code_generator/scripts/write_raw_results.py.

The finalizer is intentionally very small and pure: it reads git state +
two marker files and writes raw_results.json. These tests exercise a real
git repository under tmp_path so we're not mocking away the interesting
behaviour.
"""

import json
import subprocess


def _run(cmd, cwd):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


def _init_repo(path):
    _run(["git", "init", "--quiet", "-b", "main"], cwd=path)
    _run(["git", "config", "user.email", "t@example.com"], cwd=path)
    _run(["git", "config", "user.name", "t"], cwd=path)
    (path / "seed.txt").write_text("seed\n")
    _run(["git", "add", "."], cwd=path)
    _run(["git", "commit", "--quiet", "-m", "seed"], cwd=path)


def _make_change(path, branch, files):
    _run(["git", "checkout", "--quiet", "-b", branch], cwd=path)
    for fname, content in files.items():
        (path / fname).write_text(content)
    _run(["git", "add", "."], cwd=path)
    _run(["git", "commit", "--quiet", "-m", f"change on {branch}"], cwd=path)


def test_write_raw_results_success_path(tmp_path):
    from orchestrator.code_generator.scripts.write_raw_results import main

    _init_repo(tmp_path)
    _make_change(tmp_path, "feature/AOS-291+test", {"foo.py": "print('hi')\n"})
    (tmp_path / ".ngb_status").write_text("success")

    out = tmp_path / "raw_results.json"
    rc = main(
        [
            "--ticket-key",
            "AOS-291",
            "--working-dir",
            str(tmp_path),
            "--output",
            str(out),
        ]
    )
    assert rc == 0

    result = json.loads(out.read_text())
    assert result["ticket_key"] == "AOS-291"
    assert result["status"] == "success"
    assert result["build"] == "pass"
    assert result["tests"] == "pass"
    assert result["branch"] == "feature/AOS-291+test"
    assert result["commit_sha"], "commit_sha should be populated"
    assert result["files_changed"] == ["foo.py"]
    assert result["diff_base_sha"], "diff_base_sha should point at the merge-base"
    assert "error" not in result


def test_write_raw_results_partial_status_maps_to_pass_fail(tmp_path):
    from orchestrator.code_generator.scripts.write_raw_results import main

    _init_repo(tmp_path)
    _make_change(tmp_path, "feature/AOS-291+partial", {"a.py": "1\n"})
    (tmp_path / ".ngb_status").write_text("partial")
    (tmp_path / ".ngb_error").write_text("2 tests failing")

    out = tmp_path / "raw_results.json"
    assert (
        main(["--ticket-key", "AOS-291", "--working-dir", str(tmp_path), "--output", str(out)]) == 0
    )

    result = json.loads(out.read_text())
    assert result["status"] == "partial"
    assert result["build"] == "pass"
    assert result["tests"] == "fail"
    assert result["error"] == "2 tests failing"


def test_write_raw_results_missing_marker_defaults_to_failed(tmp_path):
    """No .ngb_status marker → the recipe never got to Step 5. Default: failed."""
    from orchestrator.code_generator.scripts.write_raw_results import main

    _init_repo(tmp_path)  # no branch, no marker

    out = tmp_path / "raw_results.json"
    assert (
        main(["--ticket-key", "AOS-291", "--working-dir", str(tmp_path), "--output", str(out)]) == 0
    )

    result = json.loads(out.read_text())
    assert result["status"] == "failed"
    assert result["build"] == "fail"
    assert result["tests"] == "skipped"
    assert result["files_changed"] == []


def test_write_raw_results_failed_status_with_error(tmp_path):
    from orchestrator.code_generator.scripts.write_raw_results import main

    _init_repo(tmp_path)
    (tmp_path / ".ngb_status").write_text("failed")
    (tmp_path / ".ngb_error").write_text("could not run test suite")

    out = tmp_path / "raw_results.json"
    assert (
        main(["--ticket-key", "AOS-291", "--working-dir", str(tmp_path), "--output", str(out)]) == 0
    )

    result = json.loads(out.read_text())
    assert result["status"] == "failed"
    assert result["error"] == "could not run test suite"


def test_write_raw_results_returns_2_on_missing_working_dir(tmp_path):
    from orchestrator.code_generator.scripts.write_raw_results import main

    rc = main(
        [
            "--ticket-key",
            "AOS-291",
            "--working-dir",
            str(tmp_path / "does-not-exist"),
            "--output",
            str(tmp_path / "out.json"),
        ]
    )
    assert rc == 2


def test_write_raw_results_ignores_unrecognised_status_value(tmp_path):
    """A bogus marker value shouldn't crash — default to failed."""
    from orchestrator.code_generator.scripts.write_raw_results import main

    _init_repo(tmp_path)
    (tmp_path / ".ngb_status").write_text("kinda-worked")

    out = tmp_path / "raw_results.json"
    assert (
        main(["--ticket-key", "AOS-291", "--working-dir", str(tmp_path), "--output", str(out)]) == 0
    )

    result = json.loads(out.read_text())
    assert result["status"] == "failed"
