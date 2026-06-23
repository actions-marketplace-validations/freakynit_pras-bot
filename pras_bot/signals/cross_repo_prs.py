"""signal: cross_repo_prs — score based on how many PRs the author opened
across *all* public repos in the recent lookback window.

Rationale: A single author opening many PRs across many repos in a short
timeframe is a strong spam signal (e.g. "PR for Hacktoberfest" farming).
"""

from __future__ import annotations

from .base import ScoredSignal, linear


class CrossRepoPRsSignal(ScoredSignal):
    # NOTE: the auto-derived name() would mangle the "PRs" acronym into
    # "cross_repo_p_rs". Override so it matches the config key exactly.
    @classmethod
    def name(cls) -> str:
        return "cross_repo_prs"

    def score(self) -> float:
        username: str | None = (
            (self.pr_data.get("user") or {}).get("login")
        )
        if not username:
            return 50.0

        sig_cfg = self.config.get("signals", {}).get(self.name(), {})
        lookback = sig_cfg.get("lookback_days", 7)

        try:
            count = self.gh.search_prs_by_author(username, lookback_days=lookback)
        except Exception as exc:
            print(f"⚠️  cross_repo_prs: search failed ({exc!r}); using neutral score")
            return 50.0

        thresholds = sig_cfg.get("thresholds", {})
        low_max = thresholds.get("low_max", 2)
        med_max = thresholds.get("med_max", 5)
        high_max = thresholds.get("high_max", 10)

        if count <= low_max:
            return max(0.0, count * 5)       # 0→0, 1→5, 2→10
        elif count <= med_max:
            return linear(count, low_max, med_max, 10, 35)
        elif count <= high_max:
            return linear(count, med_max, high_max, 35, 70)
        else:
            return min(100.0, 70 + (count - high_max) * 4)
