"""Tests for ``ace items list`` and ``ace items show`` CLI commands.

Injects a fake :class:`AgentContextEngineService` via
``runner.invoke(run, args, obj=fake_service)`` — the same seam used by
``tests/test_ace_cli_mine.py``.  No pipeline or repository code is exercised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import pytest
from click.testing import CliRunner

from ace.cli.run import run
from ace.service import (
    ItemSummaryDTO,
    ListItemsRequest,
    ListItemsResult,
    MineRequest,
    MineResult,
    ProvenanceEntryDTO,
    ShowItemRequest,
    ShowItemResult,
)

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

_ITEM_A = ItemSummaryDTO(
    id="aaaaaaaa-0000-0000-0000-000000000001",
    pattern_type="approach",
    scope="codebase_wide",
    scope_value=None,
    description="Always write tests first",
    confidence=0.92,
    confidence_tier="ESTABLISHED",
    status="active",
    last_validated="2026-01-01T00:00:00+00:00",
)

_ITEM_B = ItemSummaryDTO(
    id="bbbbbbbb-0000-0000-0000-000000000002",
    pattern_type="concern",
    scope="task_type",
    scope_value="migration",
    description="Be careful with schema migrations",
    confidence=0.75,
    confidence_tier="PATTERN",
    status="active",
    last_validated="2026-01-02T00:00:00+00:00",
)

_PROV_A = ProvenanceEntryDTO(
    signal_source="reflector",
    workflow_date="2026-01-01",
    contributed_confidence=0.30,
    workflow_id="wf-111",
    ticket_key="AOS-1",
    signal_detail="PR comment suggested TDD",
)

_SHOW_A = ShowItemResult(
    id=_ITEM_A.id,
    pattern_type=_ITEM_A.pattern_type,
    scope=_ITEM_A.scope,
    scope_value=_ITEM_A.scope_value,
    description=_ITEM_A.description,
    confidence=_ITEM_A.confidence,
    confidence_tier=_ITEM_A.confidence_tier,
    status=_ITEM_A.status,
    last_validated=_ITEM_A.last_validated,
    created_at="2026-01-01T00:00:00+00:00",
    updated_at="2026-01-01T00:00:00+00:00",
    provenance=(_PROV_A,),
    conflicts_with=(),
    project="AOS",
    repo=None,
    platform=None,
)


@dataclass
class FakeService:
    """Fake :class:`AgentContextEngineService` for CLI tests."""

    list_result: ListItemsResult = field(default_factory=lambda: ListItemsResult(items=()))
    show_result: Optional[ShowItemResult] = None
    list_calls: List[ListItemsRequest] = field(default_factory=list)
    show_calls: List[ShowItemRequest] = field(default_factory=list)

    def mine(self, request: MineRequest) -> MineResult:  # pragma: no cover
        raise NotImplementedError

    def list_items(self, request: ListItemsRequest) -> ListItemsResult:
        self.list_calls.append(request)
        return self.list_result

    def show_item(self, request: ShowItemRequest) -> Optional[ShowItemResult]:
        self.show_calls.append(request)
        return self.show_result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# ``ace --help`` includes the items group
# ---------------------------------------------------------------------------


def test_ace_help_lists_items(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(run, ["--help"])
    assert result.exit_code == 0
    assert "items" in result.output


# ---------------------------------------------------------------------------
# ``ace items list`` — flag routing
# ---------------------------------------------------------------------------


def test_items_list_no_flags_routes_to_service(cli_runner: CliRunner) -> None:
    fake = FakeService(list_result=ListItemsResult(items=(_ITEM_A,)))
    result = cli_runner.invoke(run, ["items", "list"], obj=fake)

    assert result.exit_code == 0, result.output
    assert len(fake.list_calls) == 1
    req = fake.list_calls[0]
    assert isinstance(req, ListItemsRequest)
    assert req.status is None
    assert req.pattern_type is None
    assert req.scope is None
    assert req.confidence_tier is None


def test_items_list_status_flag(cli_runner: CliRunner) -> None:
    fake = FakeService(list_result=ListItemsResult(items=()))
    result = cli_runner.invoke(run, ["items", "list", "--status", "staged"], obj=fake)
    assert result.exit_code == 0, result.output
    assert fake.list_calls[0].status == "staged"


def test_items_list_pattern_type_flag(cli_runner: CliRunner) -> None:
    fake = FakeService(list_result=ListItemsResult(items=(_ITEM_A,)))
    result = cli_runner.invoke(run, ["items", "list", "--pattern-type", "approach"], obj=fake)
    assert result.exit_code == 0, result.output
    assert fake.list_calls[0].pattern_type == "approach"


def test_items_list_scope_flag(cli_runner: CliRunner) -> None:
    fake = FakeService(list_result=ListItemsResult(items=()))
    result = cli_runner.invoke(run, ["items", "list", "--scope", "codebase_wide"], obj=fake)
    assert result.exit_code == 0, result.output
    assert fake.list_calls[0].scope == "codebase_wide"


def test_items_list_tier_flag(cli_runner: CliRunner) -> None:
    fake = FakeService(list_result=ListItemsResult(items=(_ITEM_A,)))
    result = cli_runner.invoke(run, ["items", "list", "--tier", "ESTABLISHED"], obj=fake)
    assert result.exit_code == 0, result.output
    assert fake.list_calls[0].confidence_tier == "ESTABLISHED"


def test_items_list_tier_flag_case_insensitive(cli_runner: CliRunner) -> None:
    fake = FakeService(list_result=ListItemsResult(items=()))
    result = cli_runner.invoke(run, ["items", "list", "--tier", "pattern"], obj=fake)
    assert result.exit_code == 0, result.output
    assert fake.list_calls[0].confidence_tier == "PATTERN"


def test_items_list_rejects_invalid_status(cli_runner: CliRunner) -> None:
    fake = FakeService()
    result = cli_runner.invoke(run, ["items", "list", "--status", "unknown"], obj=fake)
    assert result.exit_code != 0
    assert fake.list_calls == []


def test_items_list_rejects_invalid_tier(cli_runner: CliRunner) -> None:
    fake = FakeService()
    result = cli_runner.invoke(run, ["items", "list", "--tier", "EXCELLENT"], obj=fake)
    assert result.exit_code != 0
    assert fake.list_calls == []


# ---------------------------------------------------------------------------
# ``ace items list`` — output formatting
# ---------------------------------------------------------------------------


def test_items_list_empty_prints_message(cli_runner: CliRunner) -> None:
    fake = FakeService(list_result=ListItemsResult(items=()))
    result = cli_runner.invoke(run, ["items", "list"], obj=fake)
    assert result.exit_code == 0
    assert "no items found" in result.output


def test_items_list_renders_id_prefix_and_pattern_type(cli_runner: CliRunner) -> None:
    fake = FakeService(list_result=ListItemsResult(items=(_ITEM_A, _ITEM_B)))
    result = cli_runner.invoke(run, ["items", "list"], obj=fake)
    assert result.exit_code == 0, result.output
    # ID prefix (first 8 chars)
    assert "aaaaaaaa" in result.output
    assert "bbbbbbbb" in result.output
    assert "approach" in result.output
    assert "concern" in result.output
    assert "ESTABLISHED" in result.output
    assert "PATTERN" in result.output


def test_items_list_renders_header(cli_runner: CliRunner) -> None:
    fake = FakeService(list_result=ListItemsResult(items=(_ITEM_A,)))
    result = cli_runner.invoke(run, ["items", "list"], obj=fake)
    assert "PATTERN_TYPE" in result.output
    assert "TIER" in result.output
    assert "STATUS" in result.output


# ---------------------------------------------------------------------------
# ``ace items show`` — flag routing
# ---------------------------------------------------------------------------


def test_items_show_passes_item_id_to_service(cli_runner: CliRunner) -> None:
    fake = FakeService(show_result=_SHOW_A)
    result = cli_runner.invoke(run, ["items", "show", _ITEM_A.id], obj=fake)
    assert result.exit_code == 0, result.output
    assert len(fake.show_calls) == 1
    assert fake.show_calls[0].item_id == _ITEM_A.id


def test_items_show_not_found_exits_nonzero(cli_runner: CliRunner) -> None:
    fake = FakeService(show_result=None)
    result = cli_runner.invoke(run, ["items", "show", "nonexistent-id"], obj=fake)
    assert result.exit_code != 0


def test_items_show_requires_item_id_argument(cli_runner: CliRunner) -> None:
    fake = FakeService()
    result = cli_runner.invoke(run, ["items", "show"], obj=fake)
    assert result.exit_code != 0
    assert fake.show_calls == []


# ---------------------------------------------------------------------------
# ``ace items show`` — output formatting
# ---------------------------------------------------------------------------


def test_items_show_renders_description(cli_runner: CliRunner) -> None:
    fake = FakeService(show_result=_SHOW_A)
    result = cli_runner.invoke(run, ["items", "show", _ITEM_A.id], obj=fake)
    assert result.exit_code == 0, result.output
    assert "Always write tests first" in result.output


def test_items_show_renders_provenance_event(cli_runner: CliRunner) -> None:
    fake = FakeService(show_result=_SHOW_A)
    result = cli_runner.invoke(run, ["items", "show", _ITEM_A.id], obj=fake)
    assert result.exit_code == 0, result.output
    assert "reflector" in result.output
    assert "2026-01-01" in result.output
    assert "+0.30" in result.output


def test_items_show_renders_ticket_key_and_workflow(cli_runner: CliRunner) -> None:
    fake = FakeService(show_result=_SHOW_A)
    result = cli_runner.invoke(run, ["items", "show", _ITEM_A.id], obj=fake)
    assert "wf-111" in result.output
    assert "AOS-1" in result.output


def test_items_show_renders_confidence_and_tier(cli_runner: CliRunner) -> None:
    fake = FakeService(show_result=_SHOW_A)
    result = cli_runner.invoke(run, ["items", "show", _ITEM_A.id], obj=fake)
    assert "0.92" in result.output
    assert "ESTABLISHED" in result.output


def test_items_show_renders_no_provenance_message_when_empty(cli_runner: CliRunner) -> None:
    empty_prov = ShowItemResult(
        id=_ITEM_A.id,
        pattern_type="approach",
        scope="codebase_wide",
        scope_value=None,
        description="desc",
        confidence=0.91,
        confidence_tier="ESTABLISHED",
        status="active",
        last_validated="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        provenance=(),
        conflicts_with=(),
        project=None,
        repo=None,
        platform=None,
    )
    fake = FakeService(show_result=empty_prov)
    result = cli_runner.invoke(run, ["items", "show", _ITEM_A.id], obj=fake)
    assert result.exit_code == 0, result.output
    assert "(none)" in result.output


def test_items_show_renders_conflicts_with(cli_runner: CliRunner) -> None:
    with_conflict = ShowItemResult(
        id=_ITEM_A.id,
        pattern_type="approach",
        scope="codebase_wide",
        scope_value=None,
        description="desc",
        confidence=0.91,
        confidence_tier="ESTABLISHED",
        status="conflicted",
        last_validated="2026-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        provenance=(),
        conflicts_with=("cccccccc-0000-0000-0000-000000000003",),
        project=None,
        repo=None,
        platform=None,
    )
    fake = FakeService(show_result=with_conflict)
    result = cli_runner.invoke(run, ["items", "show", _ITEM_A.id], obj=fake)
    assert result.exit_code == 0, result.output
    assert "conflicts_with" in result.output
    assert "cccccccc" in result.output
