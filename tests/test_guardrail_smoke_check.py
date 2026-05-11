import os
from unittest.mock import Mock, patch

from scripts import guardrail_smoke_check


def test_get_staged_files_parses_output():
    mock = Mock(returncode=0, stdout="recipes/plan.yaml\nREADME.md\n", stderr="")
    with patch("subprocess.run", return_value=mock):
        files = guardrail_smoke_check.get_staged_files()
    assert files == ["recipes/plan.yaml", "README.md"]


def test_main_skips_when_no_watched_files(capsys):
    with patch.object(guardrail_smoke_check, "get_staged_files", return_value=["README.md"]):
        rc = guardrail_smoke_check.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "skipped" in out.lower()


def test_main_runs_smoke_with_changed_files_threaded(capsys):
    with (
        patch.object(
            guardrail_smoke_check,
            "get_staged_files",
            return_value=["README.md", "recipes/plan.yaml", "config/developer-rules.json"],
        ),
        patch("pathlib.Path.exists", return_value=True),
        patch.object(guardrail_smoke_check, "run_smoke_check", return_value=0) as run_mock,
    ):
        rc = guardrail_smoke_check.main()

    out = capsys.readouterr().out
    assert rc == 0
    run_mock.assert_called_once_with(["recipes/plan.yaml", "config/developer-rules.json"])
    assert "recipes/plan.yaml" in out


def test_main_failure_message_when_smoke_fails(capsys):
    with (
        patch.object(
            guardrail_smoke_check, "get_staged_files", return_value=["recipes/execute.yaml"]
        ),
        patch("pathlib.Path.exists", return_value=True),
        patch.object(guardrail_smoke_check, "run_smoke_check", return_value=1),
    ):
        rc = guardrail_smoke_check.main()
    out = capsys.readouterr().out
    assert rc == 1
    assert "failed" in out.lower()
    assert "SKIP=guardrail-smoke-check" in out


def test_run_smoke_check_sets_python_and_changed_files_param():
    with patch("subprocess.run") as run:
        run.return_value = Mock(returncode=0)
        changed = ["recipes/plan.yaml", "config/developer-rules.json"]
        rc = guardrail_smoke_check.run_smoke_check(changed)

    assert rc == 0
    _, kwargs = run.call_args
    cmd = run.call_args.args[0]
    assert "--param" in cmd
    assert "changed_files=recipes/plan.yaml,config/developer-rules.json" in cmd
    assert kwargs["env"]["GOOSE_MCP_PYTHON"] == os.sys.executable
