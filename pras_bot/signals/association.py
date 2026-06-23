"""signal: association — score based on the author's association with this repo.

Rationale: Owners / members / collaborators are trusted. A first-time
contributor with no prior merged work is more likely to be a drive-by or
spam account. The association is already present on the PR payload, so
this signal costs no extra API calls.

Covers (from the contributor-trust list):
  * "Contributor is owner, member, collaborator, or previous contributor"
  * "First-time contributor with large or risky PR"  (the first-time part)
"""

from __future__ import annotations

from .base import ScoredSignal


class AssociationSignal(ScoredSignal):
    def score(self) -> float:
        assoc: str = self.pr_data.get("author_association") or "NONE"

        sig_cfg = self.config.get("signals", {}).get(self.name(), {})
        scores: dict[str, float] = sig_cfg.get("scores", {})
        default = sig_cfg.get("default", 80.0)

        return float(scores.get(assoc, default))
