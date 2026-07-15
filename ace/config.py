"""ACE feature flags, thresholds, and tier boundaries."""

from __future__ import annotations

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
