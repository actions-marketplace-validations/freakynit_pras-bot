"""signal: issue_participation — score based on whether the author has
participated in issue discussions in *this* repo before opening the PR.

Rationale: Legitimate contributors usually discuss an issue before opening
a PR. Spam accounts open PRs without ever engaging in issues.

Note: this counts issue comments by the author in the repo (a proxy for
"discussion before PR"); GitHub search cannot filter by comment date, so
this is all-time participation rather than strictly before this PR.

Covers:
  * "Has issue discussion before opening PR"   (positive)
  * "Opens PRs without participating in issues" (negative)
"""

from __future__ import annotations

from .base import ScoredSignal, linear


class IssueParticipationSignal(ScoredSignal):
    def score(self) -> float:
        username: str | None = (self.pr_data.get("user") or {}).get("login")
        if not username:
            return 50.0

        sig_cfg = self.config.get("signals", {}).get(self.name(), {})
        thresholds = sig_cfg.get("thresholds", {})
        none_max = thresholds.get("none_max", 0)
        few_max = thresholds.get("few_max", 1)
        some_max = thresholds.get("some_max", 5)

        try:
            count = self.gh.count_issue_comments_in_repo(username)
        except Exception as exc:
            print(f"⚠️  issue_participation: lookup failed ({exc!r}); using neutral score")
            return 50.0

        if count <= none_max:
            return 70.0
        if count <= few_max:
            return linear(count, none_max, few_max, 70.0, 35.0)
        if count <= some_max:
            return linear(count, few_max, some_max, 35.0, 12.0)
        return max(0.0, 12.0 - (count - some_max) * 1.0)
