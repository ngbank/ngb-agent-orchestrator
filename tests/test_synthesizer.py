"""Unit tests for ace.retrieval.synthesizer.

The LLM is mocked at ``ace.retrieval.synthesizer.litellm.completion`` — no
network calls, no API keys required.  The SQLite cache is exercised against a
real in-memory database (via the test suite's state-store fixtures).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from ace.models import ContextItem, ProvenanceEntry
from ace.retrieval.synthesizer import (
    SynthesizedBlock,
    SynthesizerError,
    TicketContext,
    _make_cache_key,
    _parse_markdown_sections,
    _parse_response,
    synthesize_context_block,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(
    id: str = "item-1",
    description: str = "Use dependency injection for service wiring.",
    pattern_type: str = "approach",
    confidence: float = 0.85,
    conflicts_with: list[str] | None = None,
    updated_at: str = "2026-01-01T00:00:00",
) -> ContextItem:
    return ContextItem(
        id=id,
        pattern_type=pattern_type,  # type: ignore[arg-type]
        scope="codebase_wide",
        description=description,
        confidence=confidence,
        last_validated="2026-01-01T00:00:00",
        created_at="2026-01-01T00:00:00",
        updated_at=updated_at,
        provenance=[ProvenanceEntry("test_source", "2026-01-01", 0.8)],
        conflicts_with=conflicts_with or [],
    )


def _make_ticket_context(**kwargs: Any) -> TicketContext:
    defaults = dict(
        ticket_key="AOS-99",
        ticket_summary="Implement auth module",
        repo="ngb-agent-orchestrator",
        project="AOS",
        platform=None,
        recipe_target="planner",
    )
    defaults.update(kwargs)
    return TicketContext(**defaults)  # type: ignore[arg-type]


def _mock_llm_response(content: str) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def _valid_llm_response(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "sections": {
            "development_rules": "- Always inject dependencies via constructor.",
            "architectural_approach": "- Place service interfaces in a dedicated package.",
        },
        "provenance": {
            "development_rules": ["item-1"],
            "architectural_approach": ["item-1"],
        },
    }
    base.update(overrides)
    return json.dumps(base)


@pytest.fixture(autouse=True)
def _set_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACE_SYNTHESIZER_MODEL", "openai/gpt-4o")
    monkeypatch.delenv("GOOSE_MODEL", raising=False)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_synthesize_returns_synthesized_block():
    item = _make_item()
    ctx = _make_ticket_context()

    with patch(
        "ace.retrieval.synthesizer.litellm.completion",
        return_value=_mock_llm_response(_valid_llm_response()),
    ):
        result = synthesize_context_block([item], ctx)

    assert isinstance(result, SynthesizedBlock)
    assert "development_rules" in result.sections
    assert "item-1" in result.provenance.get("development_rules", [])


def test_synthesize_empty_items_returns_empty_block_without_llm_call():
    ctx = _make_ticket_context()
    with patch("ace.retrieval.synthesizer.litellm.completion") as mock_llm:
        result = synthesize_context_block([], ctx)

    assert result.is_empty()
    mock_llm.assert_not_called()


def test_synthesize_multi_item_paraphrase_collapse():
    """Two semantically similar items should produce one consolidated section."""
    items = [
        _make_item("item-1", "Inject dependencies via constructor.", confidence=0.90),
        _make_item("item-2", "Use constructor injection for all services.", confidence=0.75),
    ]
    ctx = _make_ticket_context()

    llm_body = json.dumps(
        {
            "sections": {
                "development_rules": "- Always use constructor injection for service wiring."
            },
            "provenance": {"development_rules": ["item-1", "item-2"]},
        }
    )

    with patch(
        "ace.retrieval.synthesizer.litellm.completion",
        return_value=_mock_llm_response(llm_body),
    ):
        result = synthesize_context_block(items, ctx)

    assert "development_rules" in result.sections
    ids = result.provenance.get("development_rules", [])
    assert "item-1" in ids and "item-2" in ids


def test_synthesize_conflicts_with_passthrough():
    """Items with conflicts_with should result in both ids in the payload sent to the LLM."""
    item_a = _make_item("item-a", "Prefer async handlers.", conflicts_with=["item-b"])
    item_b = _make_item("item-b", "Prefer sync handlers.", conflicts_with=["item-a"])
    ctx = _make_ticket_context()

    captured: dict[str, Any] = {}

    def _fake_completion(**kwargs: Any) -> MagicMock:
        captured["messages"] = kwargs.get("messages", [])
        return _mock_llm_response(_valid_llm_response())

    with patch("ace.retrieval.synthesizer.litellm.completion", side_effect=_fake_completion):
        synthesize_context_block([item_a, item_b], ctx)

    user_content = json.loads(captured["messages"][1]["content"])
    item_ids_in_payload = {i["id"] for i in user_content["items"]}
    assert {"item-a", "item-b"} == item_ids_in_payload
    conflicts_a = next(i["conflicts_with"] for i in user_content["items"] if i["id"] == "item-a")
    assert "item-b" in conflicts_a


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


def test_cache_hit_skips_llm(tmp_path: Any, monkeypatch: pytest.MonkeyPatch):
    """Second call with same items/context uses cache; LLM called only once."""
    item = _make_item()
    ctx = _make_ticket_context()

    call_count = 0

    def _counting_completion(**kwargs: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        return _mock_llm_response(_valid_llm_response())

    with patch("ace.retrieval.synthesizer.litellm.completion", side_effect=_counting_completion):
        first = synthesize_context_block([item], ctx)
        second = synthesize_context_block([item], ctx)

    assert call_count == 1
    assert first.sections == second.sections


def test_cache_miss_on_corpus_change():
    """Changing updated_at on any item produces a new cache key (cache miss)."""
    item_v1 = _make_item(updated_at="2026-01-01T00:00:00")
    item_v2 = _make_item(updated_at="2026-06-01T00:00:00")
    ctx = _make_ticket_context()

    key_v1 = _make_cache_key(ctx, item_v1.updated_at)
    key_v2 = _make_cache_key(ctx, item_v2.updated_at)

    assert key_v1 != key_v2


def test_cache_miss_on_recipe_target_change():
    ctx_planner = _make_ticket_context(recipe_target="planner")
    ctx_codegen = _make_ticket_context(recipe_target="code_generator")
    snapshot = "2026-01-01T00:00:00"

    assert _make_cache_key(ctx_planner, snapshot) != _make_cache_key(ctx_codegen, snapshot)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def test_parse_response_valid_json():
    raw = _valid_llm_response()
    block = _parse_response(raw)
    assert "development_rules" in block.sections
    assert block.provenance["development_rules"] == ["item-1"]


def test_parse_response_tolerates_markdown_fence():
    raw = "```json\n" + _valid_llm_response() + "\n```"
    block = _parse_response(raw)
    assert "development_rules" in block.sections


def test_parse_response_ignores_empty_sections():
    raw = json.dumps(
        {
            "sections": {"development_rules": "", "architectural_approach": "- Use layers."},
            "provenance": {"architectural_approach": ["item-1"]},
        }
    )
    block = _parse_response(raw)
    assert "development_rules" not in block.sections
    assert "architectural_approach" in block.sections


def test_parse_response_raises_on_invalid_json():
    with pytest.raises(SynthesizerError, match="invalid JSON"):
        _parse_response("not json at all")


# ---------------------------------------------------------------------------
# SynthesizedBlock.to_markdown
# ---------------------------------------------------------------------------


def test_to_markdown_renders_all_sections():
    block = SynthesizedBlock(
        sections={
            "development_rules": "- Rule A.",
            "known_pitfalls": "- Watch out for X.",
        },
        provenance={"development_rules": ["id1"], "known_pitfalls": ["id2"]},
    )
    md = block.to_markdown()
    assert "## Development rules" in md
    assert "## Known pitfalls" in md
    assert "## Architectural approach" not in md


def test_to_markdown_respects_section_order():
    block = SynthesizedBlock(
        sections={
            "known_pitfalls": "- Pitfall.",
            "development_rules": "- Rule.",
        }
    )
    md = block.to_markdown()
    dev_pos = md.index("## Development rules")
    pit_pos = md.index("## Known pitfalls")
    assert dev_pos < pit_pos  # development_rules always before known_pitfalls


def test_to_markdown_empty_block_returns_empty_string():
    assert SynthesizedBlock().to_markdown() == ""


# ---------------------------------------------------------------------------
# _parse_markdown_sections round-trip
# ---------------------------------------------------------------------------


def test_parse_markdown_sections_roundtrip():
    block = SynthesizedBlock(
        sections={
            "development_rules": "- Always inject.",
            "testing_approach": "- Cover all edge cases.",
        }
    )
    md = block.to_markdown()
    sections = _parse_markdown_sections(md)
    assert sections["development_rules"] == "- Always inject."
    assert sections["testing_approach"] == "- Cover all edge cases."
    assert "architectural_approach" not in sections


# ---------------------------------------------------------------------------
# LLM error handling
# ---------------------------------------------------------------------------


def test_synthesize_raises_synthesizer_error_on_llm_failure():
    item = _make_item()
    ctx = _make_ticket_context()

    with patch(
        "ace.retrieval.synthesizer.litellm.completion",
        side_effect=RuntimeError("network error"),
    ):
        with pytest.raises(SynthesizerError, match="LLM call failed"):
            synthesize_context_block([item], ctx)


def test_synthesize_raises_on_empty_llm_response():
    item = _make_item()
    ctx = _make_ticket_context()

    with patch(
        "ace.retrieval.synthesizer.litellm.completion",
        return_value=_mock_llm_response(""),
    ):
        with pytest.raises(SynthesizerError, match="empty content"):
            synthesize_context_block([item], ctx)
