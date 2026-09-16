"""Match score (0-100): weighted AI components + deterministic eligibility & compensation."""
from __future__ import annotations

from typing import Any

ELIGIBILITY_POINTS = {"eligible": 100, "likely_eligible": 80, "unclear": 50, "not_eligible": 0}


def clamp(value: Any) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


def compensation_points(
    npr_min: float | None,
    npr_max: float | None,
    employment_type: str,
    min_monthly_npr: float,
    allow_below_for: list[str],
) -> tuple[int, str]:
    low, high = npr_min, npr_max
    if low is None and high is None:
        return 60, "Compensation not stated"
    allowed_low = employment_type in allow_below_for
    reference = high if high is not None else low
    if low is not None and low >= min_monthly_npr:
        return 100, f"Pays at least NPR {int(low):,}/month"
    if high is not None and high >= min_monthly_npr:
        return 85, f"Range reaches NPR {int(high):,}/month"
    if reference >= 0.5 * min_monthly_npr:
        return (75 if allowed_low else 55), f"Below minimum (about NPR {int(reference):,}/month)"
    return (50 if allowed_low else 25), f"Well below minimum (about NPR {int(reference):,}/month)"


def compute_match_score(components: dict[str, Any], weights: dict[str, int]) -> tuple[int, dict[str, Any]]:
    breakdown: dict[str, Any] = {}
    total = 0.0
    for name, weight in weights.items():
        entry = components.get(name, {})
        score = clamp(entry.get("score") if isinstance(entry, dict) else entry)
        points = score * weight / 100
        total += points
        breakdown[name] = {
            "score": score,
            "weight": weight,
            "points": round(points, 1),
            "reasoning": entry.get("reasoning", "") if isinstance(entry, dict) else "",
        }
    return clamp(total), breakdown
