"""Regression tests for per-workflow operator log files."""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

from orchestrator.logging_setup import attach_workflow_file_handler, detach_workflow_file_handler
from orchestrator.paths import workflow_logs_dir
from otel import set_workflow_context


def test_concurrent_workflow_file_handlers_are_disjoint(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs"))
    logger = logging.getLogger("tests.workflow_file_logging")
    logger.setLevel(logging.INFO)
    release = threading.Event()
    ready = [threading.Event(), threading.Event()]

    def worker(index: int, workflow_id: str, own_message: str) -> None:
        set_workflow_context(workflow_id=workflow_id, ticket_key=f"AOS-{index}")
        handler = attach_workflow_file_handler(workflow_id)
        try:
            ready[index].set()
            release.wait(timeout=2.0)
            logger.info(own_message)
        finally:
            detach_workflow_file_handler(handler)

    thread_a = threading.Thread(target=worker, args=(0, "wf-a", "only wf-a"))
    thread_b = threading.Thread(target=worker, args=(1, "wf-b", "only wf-b"))
    thread_a.start()
    thread_b.start()

    assert ready[0].wait(timeout=2.0)
    assert ready[1].wait(timeout=2.0)
    release.set()
    thread_a.join(timeout=2.0)
    thread_b.join(timeout=2.0)

    log_a = Path(workflow_logs_dir("wf-a")) / "workflow.log"
    log_b = Path(workflow_logs_dir("wf-b")) / "workflow.log"
    text_a = log_a.read_text(encoding="utf-8")
    text_b = log_b.read_text(encoding="utf-8")

    assert "only wf-a" in text_a
    assert "only wf-b" not in text_a
    assert "only wf-b" in text_b
    assert "only wf-a" not in text_b


def test_run_and_tee_logs_subprocess_output_to_stdout_and_workflow_log(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs"))

    workflow_id = "wf-subprocess"
    set_workflow_context(workflow_id=workflow_id, ticket_key="AOS-168")
    workflow_handler = attach_workflow_file_handler(workflow_id)

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(logging.Formatter("%(message)s"))

    root = logging.getLogger()
    original_level = root.level
    root.setLevel(logging.INFO)
    root.addHandler(stdout_handler)
    try:
        from orchestrator.utils import run_and_tee

        result = run_and_tee(
            [sys.executable, "-c", "print('subprocess visible')"],
            "tests.subprocess",
        )
    finally:
        root.removeHandler(stdout_handler)
        root.setLevel(original_level)
        detach_workflow_file_handler(workflow_handler)

    assert result.returncode == 0
    assert "subprocess visible" in capsys.readouterr().out

    workflow_log = Path(workflow_logs_dir(workflow_id)) / "workflow.log"
    assert "subprocess visible" in workflow_log.read_text(encoding="utf-8")
