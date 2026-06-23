"""signal: signoff — does the PR satisfy DCO / sign-off when the repo requires it?

Rationale: Repos that enforce the Developer Certificate of Origin expect every
commit to carry a ``Signed-off-by:`` trailer. A PR missing it (when required)
fails a documented repo requirement.

This is **opt-in**: it only runs when ``required: true`` is set in config
(default ``false`` → the signal skips itself, so repos that don't enforce
sign-off are unaffected). It checks DCO ``Signed-off-by:`` trailers on the
PR's commits (one ``GET /pulls/{n}/commits`` call). It does **not** check CLA
status — that needs a dedicated CLA service (e.g. cla-assistant).

Covers:
  * "Satisfies CLA, DCO, sign-off, or other repo requirements"  (positive)
  * "Does not satisfy CLA/DCO/sign-off"                        (negative)
"""

from __future__ import annotations

import re

from .base import ScoredSignal

# A Signed-off-by trailer on its own line (DCO convention), case-insensitive.
_SIGNOFF_RE = re.compile(r"(?m)^\s*signed-off-by:\s*.+", re.IGNORECASE)


class SignoffSignal(ScoredSignal):
    def score(self) -> float | None:
        sig_cfg = self.config.get("signals", {}).get(self.name(), {})
        if not sig_cfg.get("required", False):
            return None                       # repo doesn't require sign-off → skip

        try:
            commits = self.gh.fetch_pr_commits()
        except Exception as exc:
            print(f"⚠️  signoff: fetch commits failed ({exc!r}); using neutral score")
            return 50.0
        if not commits:
            return 50.0

        missing = 0
        for c in commits:
            message = (c.get("commit", {}) or {}).get("message", "") or ""
            if not _SIGNOFF_RE.search(message):
                missing += 1

        if missing == 0:
            return 0.0                        # every commit signed off
        if missing == len(commits):
            return 85.0                       # no commit signed off
        return 70.0                           # some commits missing sign-off
