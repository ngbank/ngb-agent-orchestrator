"""Unit tests for the orchestrator.failure module."""

from __future__ import annotations

import pytest

from orchestrator.failure import (
    assert_failure_consistent,
    clear_failure,
    get_failure,
    has_failure,
    mark_failure,
)


class TestMarkFailure:
    def test_returns_both_fields(self) -> None:
        result = mark_failure("validate_input", "bad ticket")
        assert result == {"error": "bad ticket", "failed_node": "validate_input"}

    def test_rejects_empty_node(self) -> None:
        with pytest.raises(ValueError, match="non-empty node"):
            mark_failure("", "some error")

    def test_rejects_empty_error(self) -> None:
        with pytest.raises(ValueError, match="non-empty error"):
            mark_failure("some_node", "")


class TestClearFailure:
    def test_returns_none_for_both(self) -> None:
        assert clear_failure() == {"error": None, "failed_node": None}


class TestHasFailure:
    def test_true_when_error_only(self) -> None:
        assert has_failure({"error": "boom"}) is True

    def test_true_when_failed_node_only(self) -> None:
        assert has_failure({"failed_node": "generate_code"}) is True

    def test_true_when_both(self) -> None:
        assert has_failure({"error": "boom", "failed_node": "n"}) is True

    def test_false_when_neither(self) -> None:
        assert has_failure({}) is False

    def test_false_when_both_none(self) -> None:
        assert has_failure({"error": None, "failed_node": None}) is False

    def test_false_when_both_empty_strings(self) -> None:
        assert has_failure({"error": "", "failed_node": ""}) is False


class TestGetFailure:
    def test_both_fields(self) -> None:
        assert get_failure({"error": "boom", "failed_node": "n"}) == ("boom", "n")

    def test_error_only(self) -> None:
        assert get_failure({"error": "boom"}) == ("boom", None)

    def test_failed_node_only(self) -> None:
        assert get_failure({"failed_node": "n"}) == (None, "n")

    def test_neither(self) -> None:
        assert get_failure({}) == (None, None)


class TestAssertFailureConsistent:
    def test_passes_on_both_set(self) -> None:
        assert_failure_consistent({"error": "boom", "failed_node": "n"})

    def test_passes_on_neither_set(self) -> None:
        assert_failure_consistent({})

    def test_passes_on_failed_node_only(self) -> None:
        # code_generator subgraph pattern — error message lives in
        # code_generation_summary, not on state.error directly.
        assert_failure_consistent({"failed_node": "generate_code"})

    def test_rejects_error_without_failed_node(self) -> None:
        with pytest.raises(AssertionError, match="failed_node is empty"):
            assert_failure_consistent({"error": "boom"})

    def test_rejects_error_without_failed_node_when_failed_node_none(self) -> None:
        with pytest.raises(AssertionError, match="failed_node is empty"):
            assert_failure_consistent({"error": "boom", "failed_node": None})
