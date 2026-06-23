"""signal: review_engagement — score based on whether the author engages
with review feedback on their own PRs.

Rationale: A contributor who replies to review comments is constructive.
A spammer opens many PRs and never responds. We approximate engagement as
the fraction of the author's own PRs where they also left a comment.

Note: this is a proxy — GitHub search cannot distinguish "reply to review"
from any comment by the author, but a low engagement ratio is still a
useful negative signal.

Covers:
  * "Responds constructively to review feedback"        (positive)
  * "Little or no response to maintainer feedback"      (negative)
"""

from __future__ import annotations

from .base import ScoredSignal, linear


class ReviewEngagementSignal(ScoredSignal):
    def score(self) -> float:
        username: str | None = (self.pr_data.get("user") or {}).get("login")
        if not username:
            return 50.0

        sig_cfg = self.config.get("signals", {}).get(self.name(), {})
        thresholds = sig_cfg.get("thresholds", {})
        high_min = thresholds.get("high_min", 0.8)    # >= 80% engaged → low score
        med_min = thresholds.get("med_min", 0.4)       # >= 40% → medium
        low_min = thresholds.get("low_min", 0.1)       # >= 10% → medium-high
        # below low_min → high score (almost never engages)

        try:
            engaged = self.gh.count_engaged_prs(username)
            authored = self.gh.count_authored_prs(username)
        except Exception as exc:
            print(f"⚠️  review_engagement: lookup failed ({exc!r}); using neutral score")
            return 50.0

        if authored == 0:
            return 50.0                  # no PRs to measure engagement on

        ratio = engaged / authored

        if ratio >= high_min:
            return linear(ratio, high_min, 1.0, 15.0, 5.0)
        if ratio >= med_min:
            return linear(ratio, med_min, high_min, 40.0, 15.0)
        if ratio >= low_min:
            return linear(ratio, low_min, med_min, 70.0, 40.0)
        return min(100.0, 70.0 + (low_min - ratio) * 100.0)
