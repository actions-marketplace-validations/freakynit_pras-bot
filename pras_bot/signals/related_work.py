"""signal: related_work — does the author's prior work relate to this repo?

Rationale: A contributor with merged PRs in similar projects (or a history
consistent with this repo's ecosystem) is more trustworthy. Completely
unrelated work is a mild negative signal.

Provider choice (config: ``signals.related_work.provider``):
  off      – signal disabled (default)
  non_llm  – token-overlap (Jaccard) between the repo description/topics and
             the author's recent PR titles. Pure Python, no dependencies.
  llm      – configured LLM provider judges topical relatedness. Costs money/quota.

Covers:
  * "Has relevant merged PRs in similar projects"
  * "Contribution history is consistent with the repo's ecosystem"
"""

from __future__ import annotations

from .base import LLM_JSON_SYSTEM, ScoredSignal, clamp_score, linear
from ._text import tokenize
from ..json_util import extract_first_json


class RelatedWorkSignal(ScoredSignal):
    def score(self) -> float | None:
        provider = self._resolve_provider()
        if provider == "off":
            return None

        username: str | None = (self.pr_data.get("user") or {}).get("login")
        if not username:
            return 50.0

        try:
            if provider == "non_llm":
                return self._score_non_llm(username)
            return self._score_llm(username)
        except Exception as exc:
            print(f"⚠️  related_work: failed ({exc!r}); using neutral score")
            return 50.0

    # -- non-LLM: token-overlap Jaccard -----------------------------------

    def _score_non_llm(self, username: str) -> float:
        sig_cfg = self.config.get("signals", {}).get(self.name(), {})
        thresholds = sig_cfg.get("thresholds", {})
        low = thresholds.get("low_overlap", 0.05)
        high = thresholds.get("high_overlap", 0.25)

        repo_terms = self._repo_terms()
        titles = self.gh.fetch_recent_pr_titles(username, limit=30)
        author_terms = tokenize(" ".join(titles))
        if not repo_terms or not author_terms:
            return 50.0

        overlap = len(repo_terms & author_terms) / len(repo_terms | author_terms)
        if overlap <= low:
            return 70.0                  # clearly unrelated → medium-high
        if overlap >= high:
            return 15.0                  # clearly related → low (trusted)
        return linear(overlap, low, high, 70.0, 15.0)

    # -- LLM: ask configured provider ------------------------------------

    def _score_llm(self, username: str) -> float:
        repo = self.gh.fetch_repo_info()
        description = repo.get("description") or ""
        language = repo.get("language") or ""
        topics = repo.get("topics") or []
        titles = self.gh.fetch_recent_pr_titles(username, limit=20)

        prompt = (
            "You are scoring a pull-request author's relevance to a repository.\n"
            f"Repository description: {description}\n"
            f"Primary language: {language}\n"
            f"Topics: {', '.join(topics) if topics else '(none)'}\n"
            "Author's recent PR titles:\n- " + "\n- ".join(titles) + "\n\n"
            "Is the author's prior work relevant to this project's domain?\n"
            'Return ONLY a JSON object: {"score": <integer 0-100>} where '
            "0 = clearly relevant (trusted) and 100 = completely unrelated."
        )
        content = self.gh.llm_judge(prompt, system=LLM_JSON_SYSTEM)
        data = extract_first_json(content)
        return clamp_score(data.get("score"))

    # -- helpers ---------------------------------------------------------

    def _repo_terms(self) -> set[str]:
        repo = self.gh.fetch_repo_info()
        parts = [repo.get("description") or "", repo.get("language") or ""]
        parts.extend(repo.get("topics") or [])
        return tokenize(" ".join(parts))
