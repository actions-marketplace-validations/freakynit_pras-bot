"""signal: closed_unmerged_ratio — score based on the fraction of the
author's closed PRs (across all repos) that were closed WITHOUT merging.

Rationale: A high closed-unmerged ratio means the author repeatedly opens
PRs that get rejected — a strong spam / low-quality signal. A low ratio
(merged work) is a positive trust signal.

Covers:
  * "Low ratio of closed-unmerged PRs"  (positive)
  * "High closed-unmerged PR ratio"     (negative)
"""

from __future__ import annotations

from .base import ScoredSignal, linear


class ClosedUnmergedRatioSignal(ScoredSignal):
    def score(self) -> float:
        username: str | None = (self.pr_data.get("user") or {}).get("login")
        if not username:
            return 50.0

        sig_cfg = self.config.get("signals", {}).get(self.name(), {})
        thresholds = sig_cfg.get("thresholds", {})
        low_max = thresholds.get("low_max", 0.2)
        med_max = thresholds.get("med_max", 0.5)
        high_max = thresholds.get("high_max", 0.8)

        try:
            closed_unmerged = self.gh.count_closed_unmerged_prs(username)
            merged = self.gh.count_merged_prs(username)
        except Exception as exc:
            print(f"⚠️  closed_unmerged_ratio: lookup failed ({exc!r}); using neutral score")
            return 50.0

        total = closed_unmerged + merged
        if total == 0:
            return 50.0                   # no closed PRs at all → unknown

        ratio = closed_unmerged / total

        if ratio <= low_max:
            return linear(ratio, 0.0, low_max, 5.0, 25.0)
        if ratio <= med_max:
            return linear(ratio, low_max, med_max, 25.0, 55.0)
        if ratio <= high_max:
            return linear(ratio, med_max, high_max, 55.0, 85.0)
        return min(100.0, 85.0 + (ratio - high_max) * 75.0)
