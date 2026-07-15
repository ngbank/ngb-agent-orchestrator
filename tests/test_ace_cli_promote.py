"""Tests for ``ace promote`` and ``ace reject`` CLI commands.

Injects a fake :class:`AgentContextEngineService` via
``runner.invoke(run, args, obj=fake_service)`` — the same seam used by
``tests/test_ace_cli_items.py``.  No pipeline or repository code is exercised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import pytest
from click.testing import CliRunner

from ace.cli.run import run
from ace.service import (
    ListItemsRequest,
    ListItemsResult,
    MineRequest,
    MineResult,
    PromoteRequest,
    PromoteResult,
    RejectRequest,
    RejectResult,
    ShowItemRequest,
    ShowItemResult,
)

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

_STAGED_ID = "cccccccc-0000-0000-0000-000000000003"


@dataclass
class FakeService:
    """Fake :class:`AgentContextEngineService` for promote/reject CLI tests."""

    promote_result: Optional[PromoteResult] = None
    promote_error: Optional[Exception] = None
    reject_error: Optional[Exception] = None
    promote_calls: List[PromoteRequest] = field(default_factory=list)
    reject_calls: List[RejectRequest] = field(default_factory=list)

    def mine(self, request: MineRequest) -> MineResult:  # pragma: no cover
        raise NotImplementedError

    def list_items(self, request: ListItemsRequest) -> ListItemsResult:  # pragma: no cover
        raise NotImplementedError

    def show_item(self, request: ShowItemRequest) -> Optional[ShowItemResult]:  # pragma: no cover
        raise NotImplementedError

    def promote(self, request: PromoteRequest) -> PromoteResult:
        self.promote_calls.append(request)
        if self.promote_error is not None:
            raise self.promote_error
        return self.promote_result or PromoteResult(item_id=request.item_id)

    def reject(self, request: RejectRequest) -> RejectResult:
        self.reject_calls.append(request)
        if self.reject_error is not None:
            raise self.reject_error
        return RejectResult(item_id=request.item_id)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# ``ace --help`` lists promote and reject
# ---------------------------------------------------------------------------


def test_ace_help_lists_promote_and_reject(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(run, ["--help"])
    assert result.exit_code == 0
    assert "promote" in result.output
    assert "reject" in result.output


# ---------------------------------------------------------------------------
# ``ace promote`` — basic routing
# ---------------------------------------------------------------------------


def test_promote_routes_to_service(cli_runner: CliRunner) -> None:
    fake = FakeService(promote_result=PromoteResult(item_id=_STAGED_ID))
    result = cli_runner.invoke(run, ["promote", _STAGED_ID], obj=fake)

    assert result.exit_code == 0, result.output
    assert len(fake.promote_calls) == 1
    req = fake.promote_calls[0]
    assert isinstance(req, PromoteRequest)
    assert req.item_id == _STAGED_ID
    assert req.notes is None
    assert req.scope is None
    assert req.scope_value is None


def test_promote_prints_item_id(cli_runner: CliRunner) -> None:
    fake = FakeService(promote_result=PromoteResult(item_id=_STAGED_ID))
    result = cli_runner.invoke(run, ["promote", _STAGED_ID], obj=fake)
    assert result.exit_code == 0
    assert _STAGED_ID in result.output


def test_promote_passes_notes(cli_runner: CliRunner) -> None:
    fake = FakeService()
    cli_runner.invoke(run, ["promote", _STAGED_ID, "--notes", "Looks good"], obj=fake)
    assert fake.promote_calls[0].notes == "Looks good"


def test_promote_passes_scope(cli_runner: CliRunner) -> None:
    fake = FakeService()
    cli_runner.invoke(
        run,
        ["promote", _STAGED_ID, "--scope", "task_type", "--scope-value", "migration"],
        obj=fake,
    )
    req = fake.promote_calls[0]
    assert req.scope == "task_type"
    assert req.scope_value == "migration"


def test_promote_service_error_exits_nonzero(cli_runner: CliRunner) -> None:
    fake = FakeService(promote_error=ValueError("No staged context item with id 'x'"))
    result = cli_runner.invoke(run, ["promote", "x"], obj=fake)
    assert result.exit_code != 0
    assert "error:" in result.output


# ---------------------------------------------------------------------------
# ``ace reject`` — basic routing
# ---------------------------------------------------------------------------


def test_reject_routes_to_service(cli_runner: CliRunner) -> None:
    fake = FakeService()
    result = cli_runner.invoke(run, ["reject", _STAGED_ID], obj=fake)

    assert result.exit_code == 0, result.output
    assert len(fake.reject_calls) == 1
    req = fake.reject_calls[0]
    assert isinstance(req, RejectRequest)
    assert req.item_id == _STAGED_ID
    assert req.notes is None


def test_reject_prints_item_id(cli_runner: CliRunner) -> None:
    fake = FakeService()
    result = cli_runner.invoke(run, ["reject", _STAGED_ID], obj=fake)
    assert result.exit_code == 0
    assert _STAGED_ID in result.output


def test_reject_passes_notes(cli_runner: CliRunner) -> None:
    fake = FakeService()
    cli_runner.invoke(run, ["reject", _STAGED_ID, "--notes", "Insufficient evidence"], obj=fake)
    assert fake.reject_calls[0].notes == "Insufficient evidence"


def test_reject_service_error_exits_nonzero(cli_runner: CliRunner) -> None:
    fake = FakeService(reject_error=ValueError("No staged context item with id 'x'"))
    result = cli_runner.invoke(run, ["reject", "x"], obj=fake)
    assert result.exit_code != 0
    assert "error:" in result.output
