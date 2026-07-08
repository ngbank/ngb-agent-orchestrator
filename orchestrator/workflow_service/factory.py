"""Environment-driven :class:`WorkflowService` factory.

This module owns the dispatcher's local-vs-remote selection logic so the CLI,
TUI, and any future tool can route a single call (``build_workflow_service_from_env``)
to the right implementation based on ``ORCHESTRATOR_MODE``.

Env vars (read on every call so tests using ``monkeypatch.setenv`` see updates
without restarting the process):

* ``ORCHESTRATOR_MODE`` — ``local`` (default) or ``remote``.
* ``ORCHESTRATOR_URL`` — required when ``ORCHESTRATOR_MODE=remote``.  Base URL
  of the orchestrator server (e.g. ``http://orchestrator.internal:8080``).
* ``ORCHESTRATOR_TOKEN`` — optional bearer token sent on every request to the
  remote server; matches the server's ``ORCHESTRATOR_API_TOKEN``.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

from .http_workflow_service import build_http_workflow_service
from .local_workflow_service import build_local_workflow_service
from .protocols import WorkflowService

MODE_ENV = "ORCHESTRATOR_MODE"
URL_ENV = "ORCHESTRATOR_URL"
TOKEN_ENV = "ORCHESTRATOR_TOKEN"

MODE_LOCAL = "local"
MODE_REMOTE = "remote"

_GOOSE_VERSION_PATH = Path(__file__).resolve().parent.parent.parent / ".goose-version"

logger = logging.getLogger(__name__)


def _normalize_goose_version(raw: Optional[str]) -> Optional[str]:
    """Return the version string with all whitespace stripped, or ``None``.

    ``goose --version`` prints ``" 1.33.1"`` (leading space) and ``.goose-version``
    is typically ``"1.33.1\n"`` — we compare on the whitespace-stripped form so
    formatting differences do not trigger spurious drift warnings.
    """
    if raw is None:
        return None
    stripped = "".join(raw.split())
    return stripped or None


def check_goose_version_drift(expected: Optional[str], actual: Optional[str]) -> Optional[str]:
    """Return a human-readable warning if the goose versions drift.

    Both inputs are normalized before comparison.  Returns ``None`` when:

    * ``expected`` is missing (no ``.goose-version`` file — nothing to compare against)
    * ``actual`` is missing (no goose on PATH — a different error surfaces at recipe run time)
    * both values are present and match after normalization

    Otherwise returns a single-line diagnostic suitable for logging.
    """
    exp = _normalize_goose_version(expected)
    act = _normalize_goose_version(actual)
    if not exp or not act:
        return None
    if exp == act:
        return None
    return (
        f"goose CLI version drift: host has {act}, .goose-version pins {exp}. "
        "Local-mode runs will use the host version while remote-mode runs use "
        "the container's pinned version — behavior may diverge. "
        "Run './setup-env.sh --goose-force' to reinstall the pinned version."
    )


def _read_goose_version_file() -> Optional[str]:
    try:
        return _GOOSE_VERSION_PATH.read_text(encoding="utf-8")
    except OSError:
        return None


def _read_installed_goose_version() -> Optional[str]:
    try:
        result = subprocess.run(
            ["goose", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _warn_on_goose_version_drift() -> None:
    """Emit a log warning if the host goose version diverges from ``.goose-version``.

    Non-fatal.  Failures reading the pin file or invoking ``goose --version``
    are silently ignored so this never blocks a local-mode startup.
    """
    warning = check_goose_version_drift(_read_goose_version_file(), _read_installed_goose_version())
    if warning:
        logger.warning(warning)


def _resolve_mode() -> str:
    raw = (os.environ.get(MODE_ENV) or MODE_LOCAL).strip().lower()
    if raw not in (MODE_LOCAL, MODE_REMOTE):
        raise ValueError(
            f"{MODE_ENV}={raw!r} is invalid; expected '{MODE_LOCAL}' or '{MODE_REMOTE}'."
        )
    return raw


def build_workflow_service_from_env() -> WorkflowService:
    """Return the :class:`WorkflowService` implied by the process environment.

    Behaviour:

    * ``ORCHESTRATOR_MODE`` unset, empty, or ``local`` → :class:`LocalWorkflowService`.
    * ``ORCHESTRATOR_MODE=remote`` → :class:`HttpWorkflowService` targeting
      ``ORCHESTRATOR_URL`` with ``ORCHESTRATOR_TOKEN`` (when set).

    In local mode the host ``goose`` version is compared against
    ``.goose-version`` and a warning is logged on drift.  The check is best-effort
    (missing file or missing binary → no warning); it never raises.

    Raises :class:`ValueError` for unknown modes or when ``remote`` is selected
    without an ``ORCHESTRATOR_URL`` — surfaced to the CLI so the user sees a
    clear configuration error rather than a request-time failure.
    """
    mode = _resolve_mode()
    if mode == MODE_LOCAL:
        _warn_on_goose_version_drift()
        return build_local_workflow_service()

    base_url: Optional[str] = (os.environ.get(URL_ENV) or "").strip() or None
    if not base_url:
        raise ValueError(
            f"{MODE_ENV}={MODE_REMOTE} requires {URL_ENV} to be set "
            "(orchestrator server base URL)."
        )
    token: Optional[str] = (os.environ.get(TOKEN_ENV) or "").strip() or None
    return build_http_workflow_service(base_url, token=token)


__all__ = [
    "MODE_ENV",
    "URL_ENV",
    "TOKEN_ENV",
    "MODE_LOCAL",
    "MODE_REMOTE",
    "build_workflow_service_from_env",
    "check_goose_version_drift",
]
