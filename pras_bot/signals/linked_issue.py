"""signal: linked_issue — does the PR reference an issue or prior discussion?

Rationale: A PR that links to an issue / discussion (``fixes #123``,
``closes #42``, an issues URL) is well-contextualized and a positive signal.
A large change with no linked issue is a mild negative (slop PRs rarely tie
to a real bug).

No API call — checks the PR title + body already on the payload.

Covers:
  * "Existing issue explains context"                 (positive)
  * "No concrete bug, user impact, benchmark, or issue"  (negative)
  * "Small fix tied to a real issue"                  (positive)
"""

from __future__ import annotations

import re

from .base import ScoredSignal

# A GitHub issue reference: "#123", "fixes #123", "closes #42",
# "resolves #7", or an issues URL. Markdown ATX headings ("# Title") never
# match because they have no digit immediately after the "#".
_ISSUE_RE = re.compile(
    r"(?:\b(?:fix(?:e[ds])?|close[ds]?|resolv(?:e[ds])?|address(?:e[ds])?|"
    r"ref(?:erence)?[ds]?|re|see|related to)\s+)?#\d+\b"
    r"|/issues/\d+",
    re.IGNORECASE,
)


class LinkedIssueSignal(ScoredSignal):
    def score(self) -> float:
        title: str = self.pr_data.get("title") or ""
        body: str = self.pr_data.get("body") or ""
        text = f"{title}\n{body}"

        sig_cfg = self.config.get("signals", {}).get(self.name(), {})
        small_max = sig_cfg.get("small_max_lines", 50)

        if _ISSUE_RE.search(text):
            return 10.0                       # references an issue → positive

        # No reference: mild suspicion scaled by change size (a tiny fix
        # often needs no issue; a large change without one is riskier).
        size = int(self.pr_data.get("additions", 0)) + int(self.pr_data.get("deletions", 0))
        if size <= small_max:
            return 30.0
        return 55.0
