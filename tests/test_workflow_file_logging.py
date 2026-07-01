"""Regression tests for per-workflow operator log files."""

from __future__ import annotations

import logging
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
