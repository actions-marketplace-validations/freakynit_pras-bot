"""signal: duplicate_pr_titles — score based on how many of the author's
recent PR titles are near-identical.

Rationale: Spammers (especially profile farmers) often mass-file PRs with
copy-pasted or near-identical titles across many repos.

Covers:
  * "Many near-identical PR titles or bodies"
"""

from __future__ import annotations

from .base import ScoredSignal, linear
from ._text import normalize_title


class DuplicatePRTitlesSignal(ScoredSignal):
    # Override name(): the auto-derived form mangles the "PR" acronym.
    @classmethod
    def name(cls) -> str:
        return "duplicate_pr_titles"

    def score(self) -> float:
        username: str | None = (self.pr_data.get("user") or {}).get("login")
        if not username:
            return 50.0

        sig_cfg = self.config.get("signals", {}).get(self.name(), {})
        sample_size = sig_cfg.get("sample_size", 30)
        thresholds = sig_cfg.get("thresholds", {})
        low_max = thresholds.get("low_max", 0.1)
        med_max = thresholds.get("med_max", 0.3)
        high_max = thresholds.get("high_max", 0.6)

        try:
            titles = self.gh.fetch_recent_pr_titles(username, limit=sample_size)
        except Exception as exc:
            print(f"⚠️  duplicate_pr_titles: lookup failed ({exc!r}); using neutral score")
            return 50.0

        if len(titles) < 2:
            return 0.0                    # not enough PRs to detect duplication

        normalized = [normalize_title(t) for t in titles if t]
        if not normalized:
            return 0.0

        # Largest cluster of identical normalized titles, as a fraction of all.
        counts: dict[str, int] = {}
        for t in normalized:
            counts[t] = counts.get(t, 0) + 1
        max_cluster = max(counts.values())
        ratio = max_cluster / len(normalized)

        if ratio <= low_max:
            return linear(ratio, 0.0, low_max, 0.0, 10.0)
        if ratio <= med_max:
            return linear(ratio, low_max, med_max, 10.0, 45.0)
        if ratio <= high_max:
            return linear(ratio, med_max, high_max, 45.0, 80.0)
        return min(100.0, 80.0 + (ratio - high_max) * 66.0)
