"""Shared utilities for graph nodes."""

import os
import subprocess
from pathlib import Path
from typing import IO, List


def _logs_dir() -> Path:
    path = Path(os.getenv("LOGS_DIR", "logs"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_path(workflow_id: str, stage: str) -> Path:
    """Return the log file path for a given workflow and stage (e.g. 'plan', 'execute')."""
    return _logs_dir() / f"{workflow_id}_{stage}.log"


def run_and_tee(
    cmd: List[str],
    log_file: IO[str],
    **kwargs,
) -> subprocess.CompletedProcess:
    """
    Run a command, streaming stdout+stderr to both the terminal and log_file.

    Returns a CompletedProcess-like object with returncode set.
    All subprocess kwargs (cwd, env, etc.) are forwarded.
    """
    kwargs.setdefault("stdout", subprocess.PIPE)
    kwargs.setdefault("stderr", subprocess.STDOUT)

    process = subprocess.Popen(cmd, **kwargs)
    assert process.stdout is not None

    for raw_line in process.stdout:
        line = raw_line.decode(errors="replace")
        print(line, end="", flush=True)
        log_file.write(line)
        log_file.flush()

    process.wait()
    return subprocess.CompletedProcess(cmd, process.returncode)
