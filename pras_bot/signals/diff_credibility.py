"""signal: diff_credibility — do the actual changes match the PR's claims?

Rationale: Some low-quality or spammy PRs use a plausible title/body while
the diff is trivial, unrelated, or an unjustified wrapper/refactor. This
signal sends a bounded subset of GitHub's per-file ``patch`` fields to an LLM
and asks it to compare the claims against the actual changed lines.

Provider choice (config: ``signals.diff_credibility.provider``):
  off   – signal disabled (default)
  llm   – configured LLM provider judges claim-vs-diff credibility.
          Costs money/quota.

  ``non_llm`` is **not** supported — matching natural-language claims to a
  code diff needs an LLM.

Covers:
  * "PR title/body sounds useful but diff is trivial or unrelated"
  * "Adds wrappers/abstractions, or refactors without need"
  * "Security/performance claims without exploit or benchmark"
"""

from __future__ import annotations

from ._diff import patch_context_from_files, patch_context_limits
from .base import LLM_JSON_SYSTEM, ScoredSignal, clamp_score
from ..json_util import extract_first_json


class DiffCredibilitySignal(ScoredSignal):
    def score(self) -> float | None:
        provider = self._resolve_provider()
        if provider == "off":
            return None
        if provider != "llm":
            print("⚠️  diff_credibility: only provider 'llm' is supported; skipping")
            return None

        try:
            return self._score_llm()
        except Exception as exc:
            print(f"⚠️  diff_credibility: failed ({exc!r}); using neutral score")
            return 50.0

    def _score_llm(self) -> float | None:
        max_files, max_chars = patch_context_limits(self.config)
        files = self.gh.fetch_pr_files()
        patch_context = patch_context_from_files(files, max_files=max_files, max_chars=max_chars)
        if not patch_context:
            return None

        title = self.pr_data.get("title") or ""
        body = self.pr_data.get("body") or ""
        prompt = (
            "You are scoring whether a pull request's actual code diff is\n"
            "credible and substantively matches its title/body claims.\n"
            "0 = the diff clearly supports the claims and is appropriately scoped.\n"
            "100 = the diff is trivial, unrelated, misleading, or makes broad\n"
            "security/performance/refactor claims without evidence in the diff.\n\n"
            f"PR title: {title}\n"
            f"PR body (truncated):\n{body[:3000]}\n\n"
            f"Selected PR patches (bounded):\n{patch_context}\n\n"
            'Return ONLY a JSON object: {"score": <integer 0-100>, "reason": "<short>"}'
        )
        content = self.gh.llm_judge(prompt, system=LLM_JSON_SYSTEM)
        data = extract_first_json(content)
        return clamp_score(data.get("score"))
