"""Shared log path helpers.

Default base directory follows XDG state conventions:

  - $XDG_STATE_HOME/ngb-agent-orchestrator/logs
  - ~/.local/state/ngb-agent-orchestrator/logs (fallback)

Set LOGS_DIR to override the base explicitly.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR_NAME = "ngb-agent-orchestrator"


def logs_base_dir() -> Path:
    """Return the configured logs base directory."""
    override = os.getenv("LOGS_DIR")
    if override:
        return Path(override).expanduser()

    xdg_state_home = os.getenv("XDG_STATE_HOME")
    if xdg_state_home:
        return Path(xdg_state_home).expanduser() / APP_DIR_NAME / "logs"

    return Path.home() / ".local" / "state" / APP_DIR_NAME / "logs"


def workflow_logs_dir(workflow_id: str, ensure_dir: bool = True) -> Path:
    """Return per-workflow logs directory under the configured base."""
    path = logs_base_dir() / workflow_id
    if ensure_dir:
        path.mkdir(parents=True, exist_ok=True)
    return path
