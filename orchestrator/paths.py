"""Shared state + log path helpers.

The orchestrator stores all persistent artefacts (SQLite DB, run logs) under a
single XDG-state parent directory so the host CLI and the containerised server
share one notion of "where state lives":

  - $XDG_STATE_HOME/ngb-agent-orchestrator/
  - ~/.local/state/ngb-agent-orchestrator/        (fallback)

Subdirectories:

  - <state-base>/db/local.db   — SQLite database (override with DB_PATH)
  - <state-base>/logs/         — per-workflow run logs (override with LOGS_DIR)
"""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR_NAME = "ngb-agent-orchestrator"


def state_base_dir() -> Path:
    """Return the shared XDG state base directory for the orchestrator.

    Resolution order:
      1. ``$XDG_STATE_HOME/ngb-agent-orchestrator`` when ``XDG_STATE_HOME`` is set.
      2. ``~/.local/state/ngb-agent-orchestrator`` otherwise.

    This helper does not honour ``DB_PATH`` or ``LOGS_DIR``; those overrides are
    applied by the subsystem-specific resolvers (``get_db_path``,
    ``logs_base_dir``).
    """
    xdg_state_home = os.getenv("XDG_STATE_HOME")
    if xdg_state_home:
        return Path(xdg_state_home).expanduser() / APP_DIR_NAME

    return Path.home() / ".local" / "state" / APP_DIR_NAME


def logs_base_dir() -> Path:
    """Return the configured logs base directory."""
    override = os.getenv("LOGS_DIR")
    if override:
        return Path(override).expanduser()

    return state_base_dir() / "logs"


def proxy_sessions_dir() -> Path:
    """Return the directory used for ephemeral litellm proxy session configs.

    Follows the same XDG-state convention as ``logs_base_dir``:
      - ``$XDG_STATE_HOME/ngb-agent-orchestrator/proxy-sessions``
      - ``~/.local/state/ngb-agent-orchestrator/proxy-sessions``  (fallback)
    """
    return state_base_dir() / "proxy-sessions"


def workflow_logs_dir(workflow_id: str, ensure_dir: bool = True) -> Path:
    """Return per-workflow logs directory under the configured base."""
    path = logs_base_dir() / workflow_id
    if ensure_dir:
        path.mkdir(parents=True, exist_ok=True)
    return path
