"""Unit tests for ace.config — ACESettings and get_ace_settings()."""

from __future__ import annotations

import pytest

from ace.config import (
    TIER_TENTATIVE_MIN,
    ACESettings,
    get_ace_settings,
)

# ---------------------------------------------------------------------------
# Default state — everything off
# ---------------------------------------------------------------------------


def test_defaults_all_off(monkeypatch):
    """No env vars set → all flags False, numeric defaults intact."""
    for key in (
        "ACE_ENABLED",
        "ACE_PLANNER_ENABLED",
        "ACE_CODE_GENERATOR_ENABLED",
        "ACE_PR_RERUN_ENABLED",
        "ACE_SYNTHESIZER_ENABLED",
        "ACE_CONFIDENCE_THRESHOLD",
        "ACE_TOP_K",
    ):
        monkeypatch.delenv(key, raising=False)

    s = get_ace_settings()

    assert s.ace_enabled is False
    assert s.planner_enabled is False
    assert s.code_generator_enabled is False
    assert s.pr_rerun_enabled is False
    assert s.synthesizer_enabled is False
    assert s.confidence_threshold == TIER_TENTATIVE_MIN
    assert s.top_k == 10


# ---------------------------------------------------------------------------
# Master switch gates per-injection-point helpers
# ---------------------------------------------------------------------------


def test_is_planner_active_requires_master_flag():
    s = ACESettings(ace_enabled=False, planner_enabled=True)
    assert s.is_planner_active() is False


def test_is_planner_active_both_on():
    s = ACESettings(ace_enabled=True, planner_enabled=True)
    assert s.is_planner_active() is True


def test_is_code_generator_active_requires_master_flag():
    s = ACESettings(ace_enabled=False, code_generator_enabled=True)
    assert s.is_code_generator_active() is False


def test_is_code_generator_active_both_on():
    s = ACESettings(ace_enabled=True, code_generator_enabled=True)
    assert s.is_code_generator_active() is True


def test_is_pr_rerun_active_requires_master_flag():
    s = ACESettings(ace_enabled=False, pr_rerun_enabled=True)
    assert s.is_pr_rerun_active() is False


def test_is_pr_rerun_active_both_on():
    s = ACESettings(ace_enabled=True, pr_rerun_enabled=True)
    assert s.is_pr_rerun_active() is True


def test_is_synthesizer_active_requires_master_flag():
    s = ACESettings(ace_enabled=False, synthesizer_enabled=True)
    assert s.is_synthesizer_active() is False


def test_is_synthesizer_active_both_on():
    s = ACESettings(ace_enabled=True, synthesizer_enabled=True)
    assert s.is_synthesizer_active() is True


# ---------------------------------------------------------------------------
# Truthy env-var values all parse as True
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("truthy", ["1", "true", "True", "TRUE", "yes", "YES", "on", "ON"])
def test_ace_enabled_truthy_values(monkeypatch, truthy):
    monkeypatch.setenv("ACE_ENABLED", truthy)
    assert get_ace_settings().ace_enabled is True


@pytest.mark.parametrize("falsy", ["0", "false", "False", "no", "off", "", "random"])
def test_ace_enabled_falsy_values(monkeypatch, falsy):
    monkeypatch.setenv("ACE_ENABLED", falsy)
    assert get_ace_settings().ace_enabled is False


# ---------------------------------------------------------------------------
# Per-injection-point env vars
# ---------------------------------------------------------------------------


def test_planner_enabled_from_env(monkeypatch):
    monkeypatch.setenv("ACE_ENABLED", "1")
    monkeypatch.setenv("ACE_PLANNER_ENABLED", "1")
    s = get_ace_settings()
    assert s.planner_enabled is True
    assert s.is_planner_active() is True


def test_code_generator_enabled_from_env(monkeypatch):
    monkeypatch.setenv("ACE_ENABLED", "true")
    monkeypatch.setenv("ACE_CODE_GENERATOR_ENABLED", "yes")
    s = get_ace_settings()
    assert s.code_generator_enabled is True
    assert s.is_code_generator_active() is True


def test_pr_rerun_enabled_from_env(monkeypatch):
    monkeypatch.setenv("ACE_ENABLED", "on")
    monkeypatch.setenv("ACE_PR_RERUN_ENABLED", "1")
    s = get_ace_settings()
    assert s.pr_rerun_enabled is True
    assert s.is_pr_rerun_active() is True


def test_synthesizer_enabled_from_env(monkeypatch):
    monkeypatch.setenv("ACE_ENABLED", "1")
    monkeypatch.setenv("ACE_SYNTHESIZER_ENABLED", "true")
    s = get_ace_settings()
    assert s.synthesizer_enabled is True
    assert s.is_synthesizer_active() is True


# ---------------------------------------------------------------------------
# Numeric parameters
# ---------------------------------------------------------------------------


def test_confidence_threshold_from_env(monkeypatch):
    monkeypatch.setenv("ACE_CONFIDENCE_THRESHOLD", "0.75")
    assert get_ace_settings().confidence_threshold == 0.75


def test_top_k_from_env(monkeypatch):
    monkeypatch.setenv("ACE_TOP_K", "5")
    assert get_ace_settings().top_k == 5


def test_confidence_threshold_boundary_zero(monkeypatch):
    monkeypatch.setenv("ACE_CONFIDENCE_THRESHOLD", "0.0")
    assert get_ace_settings().confidence_threshold == 0.0


def test_confidence_threshold_boundary_one(monkeypatch):
    monkeypatch.setenv("ACE_CONFIDENCE_THRESHOLD", "1.0")
    assert get_ace_settings().confidence_threshold == 1.0


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


def test_invalid_confidence_threshold_not_float(monkeypatch):
    monkeypatch.setenv("ACE_CONFIDENCE_THRESHOLD", "not-a-float")
    with pytest.raises(ValueError, match="ACE_CONFIDENCE_THRESHOLD must be a float"):
        get_ace_settings()


def test_invalid_confidence_threshold_out_of_range(monkeypatch):
    monkeypatch.setenv("ACE_CONFIDENCE_THRESHOLD", "1.5")
    with pytest.raises(ValueError, match="ACE_CONFIDENCE_THRESHOLD must be in"):
        get_ace_settings()


def test_invalid_top_k_not_int(monkeypatch):
    monkeypatch.setenv("ACE_TOP_K", "abc")
    with pytest.raises(ValueError, match="ACE_TOP_K must be a positive integer"):
        get_ace_settings()


def test_invalid_top_k_zero(monkeypatch):
    monkeypatch.setenv("ACE_TOP_K", "0")
    with pytest.raises(ValueError, match="ACE_TOP_K must be a positive integer"):
        get_ace_settings()


def test_invalid_top_k_negative(monkeypatch):
    monkeypatch.setenv("ACE_TOP_K", "-3")
    with pytest.raises(ValueError, match="ACE_TOP_K must be a positive integer"):
        get_ace_settings()


# ---------------------------------------------------------------------------
# All flags independent — only the requested ones are on
# ---------------------------------------------------------------------------


def test_only_planner_flag_on(monkeypatch):
    monkeypatch.setenv("ACE_ENABLED", "1")
    monkeypatch.setenv("ACE_PLANNER_ENABLED", "1")
    monkeypatch.delenv("ACE_CODE_GENERATOR_ENABLED", raising=False)
    monkeypatch.delenv("ACE_PR_RERUN_ENABLED", raising=False)
    monkeypatch.delenv("ACE_SYNTHESIZER_ENABLED", raising=False)
    s = get_ace_settings()
    assert s.is_planner_active() is True
    assert s.is_code_generator_active() is False
    assert s.is_pr_rerun_active() is False
    assert s.is_synthesizer_active() is False
