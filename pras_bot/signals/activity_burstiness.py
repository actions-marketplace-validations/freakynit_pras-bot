"""signal: activity_burstiness — score based on whether the author's recent
PR activity is bursty and spread across many (often unrelated) repos.

Rationale: Legitimate contributors have activity spread over time, usually
in a focused set of repos. Spammers produce a burst of PRs across many
unrelated repos in a very short window.

Covers:
  * "Activity pattern is bursty, broad, and unrelated"      (negative)
  * "Account has normal activity spread over time"           (positive)
"""

from __future__ import annotations

from datetime import datetime, timezone

from .base import ScoredSignal


class ActivityBurstinessSignal(ScoredSignal):
    def score(self) -> float:
        username: str | None = (self.pr_data.get("user") or {}).get("login")
        if not username:
            return 50.0

        sig_cfg = self.config.get("signals", {}).get(self.name(), {})
        lookback = sig_cfg.get("lookback_days", 30)
        sample_size = sig_cfg.get("sample_size", 30)
        thresholds = sig_cfg.get("thresholds", {})
        min_count = thresholds.get("min_count", 3)
        burst_count = thresholds.get("burst_count", 5)
        burst_span_hours = thresholds.get("burst_span_hours", 24)
        broad_repos = thresholds.get("broad_repos", 3)

        try:
            activity = self.gh.fetch_recent_pr_activity(
                username, lookback_days=lookback, limit=sample_size
            )
        except Exception as exc:
            print(f"⚠️  activity_burstiness: lookup failed ({exc!r}); using neutral score")
            return 50.0

        if len(activity) < min_count:
            return 10.0                    # too little activity to be a burst

        # distinct repos (breadth)
        repos = {a["repo"] for a in activity if a.get("repo")}
        is_broad = len(repos) >= broad_repos

        # temporal clustering (burstiness): all PRs within burst_span_hours?
        timestamps: list[datetime] = []
        for a in activity:
            try:
                ts = datetime.fromisoformat(
                    (a.get("created_at") or "").replace("Z", "+00:00")
                )
                timestamps.append(ts)
            except ValueError:
                continue
        is_bursty = False
        if len(timestamps) >= burst_count:
            timestamps.sort()
            span = (timestamps[-1] - timestamps[0]).total_seconds() / 3600.0
            is_bursty = span <= burst_span_hours

        if is_bursty and is_broad:
            return 90.0
        if is_bursty:
            return 60.0
        if is_broad:
            return 35.0
        return 15.0
