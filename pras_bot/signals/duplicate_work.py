"""signal: duplicate_work — does this PR duplicate an existing in-repo PR?

Rationale: A PR whose (normalized) title exactly matches an existing PR in
*this* repo is likely duplicating ongoing or already-settled work — a
negative signal. Distinct from ``duplicate_pr_titles``, which detects the
*author* mass-filing near-identical titles across repos.

One ``search/issues`` call (cached) for recent PRs in this repo; the current
PR is excluded by number. Matching is exact on the normalized title to keep
false positives low.

Covers:
  * "Duplicates existing work"
  * "Reopens already rejected ideas"   (closed duplicate)
"""

from __future__ import annotations

from .base import ScoredSignal
from ._text import normalize_title


class DuplicateWorkSignal(ScoredSignal):
    def score(self) -> float:
        title: str = self.pr_data.get("title") or ""
        current_norm = normalize_title(title)
        if not current_norm:
            return 5.0                        # no title → can't be a duplicate

        sig_cfg = self.config.get("signals", {}).get(self.name(), {})
        sample_size = sig_cfg.get("sample_size", 50)

        try:
            prs = self.gh.fetch_repo_pr_titles(limit=sample_size)
        except Exception as exc:
            print(f"⚠️  duplicate_work: lookup failed ({exc!r}); using neutral score")
            return 50.0

        # Exclude the PR under review itself (matched by number).
        my_number = self.pr_data.get("number")
        for pr in prs:
            if my_number is not None and pr.get("number") == my_number:
                continue
            if normalize_title(pr.get("title", "")) != current_norm:
                continue
            state = (pr.get("state") or "").lower()
            if state == "open":
                return 85.0               # active duplicate of ongoing work
            if state == "closed":
                return 60.0               # redoing settled (merged or rejected) work
            return 55.0

        return 5.0                            # no duplicate found → low
