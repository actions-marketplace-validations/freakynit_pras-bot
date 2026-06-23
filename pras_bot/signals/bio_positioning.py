"""signal: bio_positioning — score based on a "generic open-source
contributor" bio combined with little accepted work.

Rationale: Spam / profile-farming accounts often advertise themselves as an
"open source contributor" while having almost no merged PRs. A genuine
contributor with the same bio is not suspicious, so accepted work lowers
the score.

Covers:
  * "Uses generic 'open source contributor' positioning but has little
     accepted work"
"""

from __future__ import annotations

from .base import ScoredSignal, linear


class BioPositioningSignal(ScoredSignal):
    def score(self) -> float:
        username: str | None = (self.pr_data.get("user") or {}).get("login")
        if not username:
            return 50.0

        sig_cfg = self.config.get("signals", {}).get(self.name(), {})
        phrases: list[str] = sig_cfg.get("generic_phrases", [])
        thresholds = sig_cfg.get("thresholds", {})
        low_merged_max = thresholds.get("low_merged_max", 2)
        med_merged_max = thresholds.get("med_merged_max", 10)

        try:
            profile = self.gh.fetch_user_profile(username)
            merged = self.gh.count_merged_prs(username)
        except Exception as exc:
            print(f"⚠️  bio_positioning: lookup failed ({exc!r}); using neutral score")
            return 50.0

        bio = (profile.get("bio") or "").lower()
        if not bio or not phrases:
            return 0.0                      # no bio / no phrases → no signal

        if not any(p.lower() in bio for p in phrases):
            return 0.0                      # bio isn't "generic positioning"

        # generic positioning + accepted work below threshold → suspicious
        if merged <= low_merged_max:
            return linear(merged, 0, low_merged_max, 90.0, 60.0) if low_merged_max else 90.0
        if merged <= med_merged_max:
            return linear(merged, low_merged_max, med_merged_max, 60.0, 20.0)
        return max(0.0, 20.0 - (merged - med_merged_max) * 1.0)
