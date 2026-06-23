"""signal: repo_merge_history — score based on the author's previously
merged PRs in *this* repository.

Rationale: A contributor who has already had PRs merged here is known and
trusted. A first-time contributor to the repo is riskier.

Covers:
  * "Has merged PRs in this repo or organization"
  * "previous contributor"
  * the inverse of "First-time contributor …"
"""

from __future__ import annotations

from .base import ScoredSignal, linear


class RepoMergeHistorySignal(ScoredSignal):
    def score(self) -> float:
        username: str | None = (self.pr_data.get("user") or {}).get("login")
        if not username:
            return 50.0

        sig_cfg = self.config.get("signals", {}).get(self.name(), {})
        thresholds = sig_cfg.get("thresholds", {})
        none_max = thresholds.get("none_max", 0)
        few_max = thresholds.get("few_max", 2)
        some_max = thresholds.get("some_max", 5)

        try:
            count = self.gh.count_merged_prs_in_repo(username)
        except Exception as exc:
            print(f"⚠️  repo_merge_history: lookup failed ({exc!r}); using neutral score")
            return 50.0

        if count <= none_max:
            return 80.0                       # no prior merged PRs in this repo
        if count <= few_max:
            return linear(count, none_max, few_max, 80, 30)
        if count <= some_max:
            return linear(count, few_max, some_max, 30, 10)
        return max(0.0, 10 - (count - some_max) * 1.0)
