import subprocess
from unittest.mock import patch

from scripts import guardrail_smoke_check as gsc


def test_should_run_smoke_test_true_for_watched_file():
    assert gsc.should_run_smoke_test(["recipes/plan.yaml"])


def test_should_run_smoke_test_false_for_unrelated_file():
    assert not gsc.should_run_smoke_test(["README.md"])


def test_run_smoke_test_missing_output_has_clear_message():
    completed = subprocess.CompletedProcess(args=["goose"], returncode=0, stdout="ok", stderr="")

    with (
        patch("scripts.guardrail_smoke_check.shutil.which", return_value="/usr/bin/goose"),
        patch("scripts.guardrail_smoke_check.subprocess.run", return_value=completed),
    ):
        ok, message = gsc.run_smoke_test(["recipes/plan.yaml"])

    assert not ok
    assert "llm_guardrail_check.txt was not created" in message


def test_main_skips_when_no_watched_files(capsys):
    code = gsc.main(["--staged-files", "README.md"])
    assert code == 0
    captured = capsys.readouterr()
    assert "Skipped" in captured.out


def test_main_runs_and_fails_with_actionable_output(capsys):
    with patch(
        "scripts.guardrail_smoke_check.run_smoke_test", return_value=(False, "bad")
    ) as mock_run:
        code = gsc.main(["--staged-files", "recipes/execute.yaml"])

    assert code == 1
    mock_run.assert_called_once_with(["recipes/execute.yaml"])
    captured = capsys.readouterr()
    assert "Watched files staged" in captured.out
    assert "bad" in captured.err
