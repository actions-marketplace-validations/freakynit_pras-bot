"""Scoring engine — aggregates weighted signals into final spam score."""

from __future__ import annotations

from typing import Any

from .signals.base import ScoredSignal


def compute_spam_score(
    signals: list[ScoredSignal],
    config: dict[str, Any],
) -> tuple[float, list[dict[str, Any]]]:
    """Return (final_score, breakdown_list).

    final_score is clamped to [0, 100].

    Each signal returns a raw score in [0, 100]. We multiply by the
    configured weight, sum, then divide by sum-of-weights to keep the
    final score in a meaningful 0-100 range.
    """
    weights: dict[str, float] = config.get("weights", {})
    total_weighted = 0.0
    total_weight = 0.0
    breakdown: list[dict[str, Any]] = []

    for sig in signals:
        raw = sig.score()           # 0-100, or None to skip
        w = weights.get(sig.name(), 1.0)
        if raw is None:
            # Signal disabled / not applicable — drop it from the average.
            breakdown.append({"name": sig.name(), "raw": None, "weight": w, "weighted": None})
            continue
        weighted = raw * w
        total_weighted += weighted
        total_weight += w
        breakdown.append({
            "name": sig.name(),
            "raw": raw,
            "weight": w,
            "weighted": weighted,
        })

    if total_weight == 0:
        return 0.0, breakdown

    final = total_weighted / total_weight
    clamped = max(0.0, min(100.0, final))
    return clamped, breakdown


def compute_labels_from_score(
    score: float,
    config: dict[str, Any],
) -> dict[str, str] | None:
    """Return the single most-severe label whose threshold *score* meets.

    Labels are checked top-down (highest threshold first); the first one
    whose threshold the score reaches is the one applied. This matches the
    documented "only one label at a time" behaviour.

    Returns ``None`` when no label applies (e.g. empty config or every
    threshold is above *score*).
    """
    labels_config: list[dict[str, Any]] = config.get("labels", [])
    sorted_labels = sorted(labels_config, key=lambda x: x.get("threshold", 0), reverse=True)
    for lbl in sorted_labels:
        if score >= lbl.get("threshold", 0):
            return lbl
    return None
