"""Regression tests for production node output routed through logging."""

import logging
from contextlib import contextmanager
from unittest.mock import patch

import pytest


def test_await_approval_logs_review_prompt(caplog):
    from orchestrator.nodes.await_approval import await_approval

    with (
        patch("orchestrator.nodes.await_approval.get_workflow", return_value=None),
        patch("orchestrator.nodes.await_approval.update_status"),
        patch(
            "orchestrator.nodes.await_approval.interrupt",
            side_effect=RuntimeError("stop after prompt"),
        ),
        caplog.at_level(logging.INFO, logger="orchestrator.nodes.await_approval"),
        pytest.raises(RuntimeError, match="stop after prompt"),
    ):
        await_approval({"workflow_id": "wf-1", "ticket_key": "AOS-166"})

    assert "WorkPlan is ready for review" in caplog.text
    assert "dispatcher --approve-plan --ticket AOS-166" in caplog.text


def test_push_and_create_pr_logs_missing_branch_warning(caplog):
    from orchestrator.code_generator.nodes.push_and_create_pr import push_and_create_pr

    state = {
        "ticket_key": "AOS-166",
        "repo_url": "https://github.com/ngbank/ngb-agent-orchestrator.git",
        "execution_summary": {"status": "success"},
    }

    with caplog.at_level(
        logging.WARNING,
        logger="orchestrator.code_generator.nodes.push_and_create_pr",
    ):
        result = push_and_create_pr(state)

    assert result["execution_summary"]["pr_url"] == ""
    assert "Skipping push/PR; missing branch or commit SHA" in caplog.text


def test_run_goose_logs_nonzero_exit(caplog, tmp_path):
    from orchestrator.code_generator.nodes.run_goose import run_goose

    work_plan_path = tmp_path / "workplan.json"
    reasoning_path = tmp_path / "reasoning.txt"
    work_plan_path.write_text('{"summary": "adopt python logging"}', encoding="utf-8")

    state = {
        "workflow_id": "wf-1",
        "ticket_key": "AOS-166",
        "working_dir": str(tmp_path),
        "work_plan_path": str(work_plan_path),
        "summary_path": str(tmp_path / "summary.json"),
        "reasoning_path": str(reasoning_path),
    }

    @contextmanager
    def noop_goose_session(*args, **kwargs):
        yield {}

    with (
        patch("orchestrator.code_generator.nodes.run_goose.run_and_tee") as run_and_tee,
        patch(
            "orchestrator.code_generator.nodes.run_goose.goose_session",
            noop_goose_session,
        ),
        caplog.at_level(
            logging.WARNING,
            logger="orchestrator.code_generator.nodes.run_goose",
        ),
    ):
        run_and_tee.return_value.returncode = 42
        run_goose(state)

    assert "Goose exited with code 42" in caplog.text
