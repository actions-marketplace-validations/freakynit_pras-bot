"""signal: pr_body_quality — is the PR body substantive or slop?

Rationale: Spam / "slop" PRs often have an empty or copy-pasted body, or one
full of polished-but-vague phrases ("improves maintainability", "enhances
performance", "follows best practices") with no concrete bug, impact, or
verification. A real PR explains what changed, why, and how it was verified.

Provider choice (config: ``signals.pr_body_quality.provider``):
  off      – signal disabled (default)
  non_llm  – body length + count of configured vague phrases. No cost.
  llm      – configured LLM provider judges slop / vagueness / shallowness.

Covers:
  * "PR body explains what changed, why, and how it was verified"  (positive)
  * "Generic PR body with polished but shallow language"          (negative)
  * "Overuse of vague phrases …"                                   (negative)
  * "No concrete bug, user impact, benchmark, or issue"           (negative)
"""

from __future__ import annotations

from .base import LLM_JSON_SYSTEM, ScoredSignal, clamp_score
from ..json_util import extract_first_json

# Default vague/slop phrases. Repo owners can override via config.
_DEFAULT_VAGUE_PHRASES = [
    "improves maintainability",
    "improves code quality",
    "improves readability",
    "enhances performance",
    "improves performance",
    "follows best practices",
    "best practices",
    "code cleanup",
    "clean up code",
    "cleaned up code",
    "refactor for readability",
    "modernize",
    "modernize code",
    "more robust",
    "more efficient",
    "better developer experience",
    "improve developer experience",
    "reduce technical debt",
    "tech debt",
    "out of the box",
]


class PrBodyQualitySignal(ScoredSignal):
    def score(self) -> float | None:
        provider = self._resolve_provider()
        if provider == "off":
            return None

        try:
            if provider == "non_llm":
                return self._score_non_llm()
            return self._score_llm()
        except Exception as exc:
            print(f"⚠️  pr_body_quality: failed ({exc!r}); using neutral score")
            return 50.0

    # -- non-LLM: length + vague-phrase count ---------------------------

    def _score_non_llm(self) -> float:
        sig_cfg = self.config.get("signals", {}).get(self.name(), {})
        # empty list → built-in defaults
        vague_phrases: list[str] = sig_cfg.get("vague_phrases") or _DEFAULT_VAGUE_PHRASES
        short_min = sig_cfg.get("short_min_chars", 50)
        med_min = sig_cfg.get("med_min_chars", 200)

        body: str = self.pr_data.get("body") or ""
        if not body.strip():
            return 80.0                       # empty body → strong negative

        length = len(body.strip())
        if length < short_min:
            base = 55.0                       # too short to be meaningful
        elif length < med_min:
            base = 35.0                        # thin
        else:
            base = 15.0                       # substantial

        low = body.lower()
        vague_hits = sum(1 for p in vague_phrases if p and p.lower() in low)
        penalty = min(45.0, vague_hits * 12.0)
        return clamp_score(base + penalty)

    # -- LLM: judge slop -------------------------------------------------

    def _score_llm(self) -> float:
        title = self.pr_data.get("title") or ""
        body = self.pr_data.get("body") or ""
        prompt = (
            "You are scoring the quality of a pull request body.\n"
            "0 = clear, substantive (concrete bug/impact, what/why/how-tested), "
            "100 = empty or slop (polished but shallow, vague buzzwords, no "
            "concrete bug/impact/verification).\n\n"
            f"PR title: {title}\n"
            f"PR body (truncated):\n{body[:3000]}\n\n"
            'Return ONLY a JSON object: {"score": <integer 0-100>, "reason": "<short>"}'
        )
        content = self.gh.llm_judge(prompt, system=LLM_JSON_SYSTEM)
        data = extract_first_json(content)
        return clamp_score(data.get("score"))
