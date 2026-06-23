"""signal: account_age — score based on how old the PR author's GitHub account is.

Rationale: Brand-new accounts are common for throwaway spam accounts.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .base import ScoredSignal, linear


class AccountAgeSignal(ScoredSignal):
    def score(self) -> float:
        username: str | None = (
            (self.pr_data.get("user") or {}).get("login")
        )
        if not username:
            return 50.0   # can't determine author → neutral suspicion

        try:
            created_str = self.gh.fetch_user_created_at(username)
        except Exception as exc:
            print(f"⚠️  account_age: lookup failed ({exc!r}); using neutral score")
            return 50.0
        if not created_str:
            return 50.0

        try:
            created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return 50.0

        age_days = (datetime.now(timezone.utc) - created).days

        sig_cfg = self.config.get("signals", {}).get(self.name(), {})
        thresholds = sig_cfg.get("thresholds", {})
        very_new = thresholds.get("very_new_days", 7)
        new_days = thresholds.get("new_days", 30)
        medium = thresholds.get("medium_days", 180)
        established = thresholds.get("established_days", 365)

        if age_days <= very_new:
            return 100.0
        elif age_days <= new_days:
            return linear(age_days, very_new + 1, new_days, 90, 60)
        elif age_days <= medium:
            return linear(age_days, new_days + 1, medium, 60, 25)
        elif age_days <= established:
            return linear(age_days, medium + 1, established, 25, 10)
        else:
            return max(0.0, 10 - (age_days - established) * 0.05)
