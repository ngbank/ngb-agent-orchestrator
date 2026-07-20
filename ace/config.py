"""ACE feature flags, thresholds, and tier boundaries."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Optional

# ---------------------------------------------------------------------------
# Confidence tier boundaries
# ---------------------------------------------------------------------------

TIER_ESTABLISHED_MIN: float = 0.90
TIER_PATTERN_MIN: float = 0.70
TIER_TENTATIVE_MIN: float = 0.50

ConfidenceTier = Literal["ESTABLISHED", "PATTERN", "TENTATIVE"]

_TIER_THRESHOLDS: list[tuple[float, ConfidenceTier]] = [
    (TIER_ESTABLISHED_MIN, "ESTABLISHED"),
    (TIER_PATTERN_MIN, "PATTERN"),
    (TIER_TENTATIVE_MIN, "TENTATIVE"),
]


def confidence_to_tier(confidence: float) -> Optional[ConfidenceTier]:
    """Map a raw confidence score to a named tier label, or ``None`` if below threshold."""
    for threshold, tier in _TIER_THRESHOLDS:
        if confidence >= threshold:
            return tier
    return None


def tier_to_confidence_range(tier: ConfidenceTier) -> tuple[float, float]:
    """Return the ``(min_inclusive, max_exclusive)`` confidence range for *tier*.

    The maximum for ``ESTABLISHED`` is capped at ``1.0`` (inclusive); all other
    tiers are exclusive of their upper bound.
    """
    if tier == "ESTABLISHED":
        return (TIER_ESTABLISHED_MIN, 1.0)
    if tier == "PATTERN":
        return (TIER_PATTERN_MIN, TIER_ESTABLISHED_MIN)
    return (TIER_TENTATIVE_MIN, TIER_PATTERN_MIN)


# ---------------------------------------------------------------------------
# Runtime feature flags and retrieval tuning
# ---------------------------------------------------------------------------

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _parse_bool(value: Optional[str]) -> bool:
    """Return ``True`` when *value* is a truthy env-var string (case-insensitive)."""
    return (value or "").lower() in _TRUTHY


@dataclass(frozen=True)
class ACESettings:
    """Runtime feature flags and retrieval tuning knobs for ACE.

    All flags default to ``False`` (ACE is fully off by default).
    ``ace_enabled`` is the master switch: when ``False``, all
    ``is_*_active()`` helpers return ``False`` regardless of the per-injection-point
    flags.  This lets operators enable individual injection points incrementally
    without risk of accidentally activating them all at once.

    ``synthesizer_enabled`` controls whether retrieved items are rendered into a
    structured synthesis block (``True``) or returned as a legacy flat list
    (``False``).  Defaults to ``False`` for reversibility until the synthesizer
    (AOS-274) is deployed.

    Load this object via :func:`get_ace_settings`; do **not** instantiate it
    directly in production code so that env-var reads are deferred to call time.
    """

    ace_enabled: bool = False
    planner_enabled: bool = False
    code_generator_enabled: bool = False
    pr_rerun_enabled: bool = False
    synthesizer_enabled: bool = False
    confidence_threshold: float = TIER_TENTATIVE_MIN
    top_k: int = 10

    # ------------------------------------------------------------------
    # Composite guards — always AND with the master flag
    # ------------------------------------------------------------------

    def is_planner_active(self) -> bool:
        """Return ``True`` only when both the master flag and the planner flag are on."""
        return self.ace_enabled and self.planner_enabled

    def is_code_generator_active(self) -> bool:
        """Return ``True`` only when both the master flag and the code-generator flag are on."""
        return self.ace_enabled and self.code_generator_enabled

    def is_pr_rerun_active(self) -> bool:
        """Return ``True`` only when both the master flag and the PR-rerun flag are on."""
        return self.ace_enabled and self.pr_rerun_enabled

    def is_synthesizer_active(self) -> bool:
        """Return ``True`` only when the master flag and the synthesizer flag are on."""
        return self.ace_enabled and self.synthesizer_enabled


def get_ace_settings() -> ACESettings:
    """Build an :class:`ACESettings` from the current environment.

    Reads environment variables on every call so that tests can manipulate
    ``os.environ`` without side effects from a module-level singleton.  Each
    injection-point caller (planner, code generator, PR re-run) invokes this
    once per workflow stage — the overhead is negligible.

    Environment variables
    ---------------------
    ``ACE_ENABLED``
        Master switch.  Set to ``1`` / ``true`` / ``yes`` / ``on`` to activate ACE.
        All other flags are ignored when this is off.  Default: off.
    ``ACE_PLANNER_ENABLED``
        Enable context injection at the planner injection point.  Default: off.
    ``ACE_CODE_GENERATOR_ENABLED``
        Enable context injection at the code-generator injection point.  Default: off.
    ``ACE_PR_RERUN_ENABLED``
        Enable context injection on PR re-run.  Default: off.
    ``ACE_SYNTHESIZER_ENABLED``
        When on, retrieved items are rendered via the injection-time synthesizer
        (AOS-274).  When off, the legacy flat-list format is used.  Default: off.
    ``ACE_CONFIDENCE_THRESHOLD``
        Minimum confidence score for retrieval.  Items below this floor are
        excluded.  Must be a float in ``[0.0, 1.0]``.
        Default: ``{tier_min}`` (``TIER_TENTATIVE_MIN``).
    ``ACE_TOP_K``
        Maximum number of context items returned per retrieval call.
        Must be a positive integer.  Default: ``10``.
    """.format(tier_min=TIER_TENTATIVE_MIN)
    raw_threshold = os.getenv("ACE_CONFIDENCE_THRESHOLD", str(TIER_TENTATIVE_MIN))
    raw_top_k = os.getenv("ACE_TOP_K", "10")

    try:
        confidence_threshold = float(raw_threshold)
    except ValueError as exc:
        raise ValueError(
            f"ACE_CONFIDENCE_THRESHOLD must be a float in [0.0, 1.0], got: {raw_threshold!r}"
        ) from exc

    if not (0.0 <= confidence_threshold <= 1.0):
        raise ValueError(
            f"ACE_CONFIDENCE_THRESHOLD must be in [0.0, 1.0], got: {confidence_threshold}"
        )

    try:
        top_k = int(raw_top_k)
    except ValueError as exc:
        raise ValueError(f"ACE_TOP_K must be a positive integer, got: {raw_top_k!r}") from exc

    if top_k < 1:
        raise ValueError(f"ACE_TOP_K must be a positive integer, got: {top_k}")

    return ACESettings(
        ace_enabled=_parse_bool(os.getenv("ACE_ENABLED")),
        planner_enabled=_parse_bool(os.getenv("ACE_PLANNER_ENABLED")),
        code_generator_enabled=_parse_bool(os.getenv("ACE_CODE_GENERATOR_ENABLED")),
        pr_rerun_enabled=_parse_bool(os.getenv("ACE_PR_RERUN_ENABLED")),
        synthesizer_enabled=_parse_bool(os.getenv("ACE_SYNTHESIZER_ENABLED")),
        confidence_threshold=confidence_threshold,
        top_k=top_k,
    )
