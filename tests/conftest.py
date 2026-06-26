"""Test-suite-wide fixtures.

Notes
-----
``dispatcher.run`` calls :func:`dotenv.load_dotenv` at import time, which seeds
``ORCHESTRATOR_MODE`` / ``ORCHESTRATOR_URL`` from the developer's local ``.env``
file.  Any test that exercises the CLI through ``CliRunner`` and does **not**
explicitly inject a ``WorkflowService`` via ``obj=`` would otherwise pick up
the developer's remote-mode wiring and try to hit ``http://localhost:8080``.

We make the test environment deterministic by forcing local mode and clearing
the remote-transport variables for every test.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_xdg_state_home(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Point XDG state at a session tmp dir and clear LOGS_DIR.

    Scope is **session** (not function) because the OTel ``BatchSpanProcessor``
    flushes spans asynchronously — flushes that land *after* a function-scoped
    monkeypatch teardown would resolve ``logs_base_dir()`` back to the
    developer's real ``~/.local/state/ngb-agent-orchestrator/`` and leak
    ``<workflow_id>/otel.jsonl`` files there.

    We intentionally do **not** restore the env vars on teardown: OTel
    registers its ``BatchSpanProcessor`` flush as an ``atexit`` handler, which
    runs *after* pytest's session-scoped finalizers.  If we restored
    ``XDG_STATE_HOME`` here, that final atexit flush would resolve
    ``logs_base_dir()`` back to the developer's real state dir and leak there.
    Leaving the vars set keeps every late flush targeted at the pytest tmp
    root, which pytest cleans up on its own retention schedule.
    """
    tmp_root = tmp_path_factory.mktemp("xdg-state-root")
    os.environ["XDG_STATE_HOME"] = str(tmp_root)
    os.environ.pop("LOGS_DIR", None)
    yield


@pytest.fixture(autouse=True)
def _isolate_orchestrator_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Default every test to ORCHESTRATOR_MODE=local and clear remote vars.

    Individual tests can still override via ``monkeypatch.setenv`` after the
    fixture runs (autouse fixtures execute before test bodies, not after).
    """
    monkeypatch.setenv("ORCHESTRATOR_MODE", "local")
    monkeypatch.delenv("ORCHESTRATOR_URL", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_TOKEN", raising=False)
    yield


@pytest.fixture(autouse=True)
def _isolate_db_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[None]:
    """Give each test a fresh, migrated SQLite DB under tmp_path.

    Without this, any test that hits the global ``state.workflow_repository``
    singleton silently reads/writes the developer's local DB at
    ``~/.local/state/ngb-agent-orchestrator/db/local.db``.

    Individual tests can still override via ``monkeypatch.setenv`` after this
    fixture runs (autouse fixtures execute before test bodies).
    """
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))

    # Lazy import so importing conftest doesn't pull SQLite at collection time.
    from state import workflow_repository as state_store

    state_store.run_migrations()
    yield
