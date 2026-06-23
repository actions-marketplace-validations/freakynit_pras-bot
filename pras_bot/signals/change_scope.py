"""signal: change_scope — does the PR sprawl across unrelated top-level areas?

Rationale: A focused PR touches one top-level area (one module / package).
A PR that fans out across many unrelated top-level directories raises review
burden and is a mild negative signal (sprawling / batched change).

Uses the PR's changed-file list (shared ``GET /pulls/{n}/files`` call).

Covers:
  * "Multiple unrelated areas changed"        (negative)
  * "Clear scope" / "Change is isolated and easy to revert"  (positive)
"""

from __future__ import annotations

from .base import ScoredSignal, linear
from ._paths import top_scope


class ChangeScopeSignal(ScoredSignal):
    def score(self) -> float:
        try:
            files = self.gh.fetch_pr_files()
        except Exception as exc:
            print(f"⚠️  change_scope: fetch files failed ({exc!r}); using neutral score")
            return 50.0
        if not files:
            return 50.0

        sig_cfg = self.config.get("signals", {}).get(self.name(), {})
        thresholds = sig_cfg.get("thresholds", {})
        low_max = thresholds.get("low_max", 1)
        med_max = thresholds.get("med_max", 2)
        high_max = thresholds.get("high_max", 4)

        scopes = {top_scope(f.get("filename", "")) for f in files}
        n = len(scopes)

        if n <= low_max:
            return 10.0                       # one area → focused
        if n <= med_max:
            return linear(n, low_max, med_max, 10.0, 30.0)
        if n <= high_max:
            return linear(n, med_max, high_max, 30.0, 65.0)
        return min(100.0, 65.0 + (n - high_max) * 8.0)
