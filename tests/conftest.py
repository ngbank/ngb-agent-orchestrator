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

from typing import Iterator

import pytest


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
