"""signal: scope_alignment — does the PR align with the project's documented
scope / roadmap / architecture?

Rationale: A PR that conflicts with the documented roadmap or reshapes the
architecture without a maintainer request is a negative signal. One that
clearly fits the project's stated scope and architecture is positive.

Reference docs: repo owner supplies one or more paths (config:
``reference_docs``), each relative to the repo root — e.g. ``ROADMAP.md``,
``ARCHITECTURE.md``, ``docs/ROADMAP.md``. Missing files (404) are skipped
silently; if no configured doc exists the signal skips itself.

Provider choice (config: ``signals.scope_alignment.provider``):
  off      – signal disabled (default)
  non_llm  – token-overlap (Jaccard) between the PR title+body and the
             reference docs. Pure Python, no cost.
  llm      – GitHub Models judges whether the PR fits the documented scope.
             Costs money/quota.

Covers:
  * "Respects project scope and roadmap"           (positive)
  * "Matches existing architecture"                (positive)
  * "Conflicts with existing roadmap or issue discussion"  (negative)
  * "Changes architecture without maintainer request"      (negative)
"""

from __future__ import annotations

from .base import LLM_JSON_SYSTEM, ScoredSignal, clamp_score, linear
from ._diff import patch_context_from_files, patch_context_limits
from ._text import tokenize
from ..json_util import extract_first_json


class ScopeAlignmentSignal(ScoredSignal):
    def score(self) -> float | None:
        provider = self._resolve_provider()
        if provider == "off":
            return None

        sig_cfg = self.config.get("signals", {}).get(self.name(), {})
        paths: list[str] = list(sig_cfg.get("reference_docs", ["ROADMAP.md", "ARCHITECTURE.md"]))

        docs = self._fetch_docs(paths)
        if not docs:
            # No scope/roadmap/architecture doc in the repo → can't judge.
            return None

        try:
            if provider == "non_llm":
                return self._score_non_llm(docs)
            return self._score_llm(docs)
        except Exception as exc:
            print(f"⚠️  scope_alignment: failed ({exc!r}); using neutral score")
            return 50.0

    # -- helpers ---------------------------------------------------------

    def _fetch_docs(self, paths: list[str]) -> str:
        """Concatenate the text of every configured doc that exists in the repo."""
        chunks: list[str] = []
        for path in paths:
            if not path:
                continue
            try:
                text = self.gh.fetch_file_text(path)
            except Exception as exc:
                print(f"⚠️  scope_alignment: fetch {path!r} failed ({exc!r}); skipping doc")
                continue
            if text:
                chunks.append(text)
        return "\n\n".join(chunks)

    # -- non-LLM: token-overlap Jaccard ----------------------------------

    def _score_non_llm(self, docs: str) -> float:
        sig_cfg = self.config.get("signals", {}).get(self.name(), {})
        thresholds = sig_cfg.get("thresholds", {})
        low = thresholds.get("low_overlap", 0.05)
        high = thresholds.get("high_overlap", 0.2)

        title = self.pr_data.get("title") or ""
        body = self.pr_data.get("body") or ""
        pr_terms = tokenize(f"{title} {body}")
        doc_terms = tokenize(docs)
        if not pr_terms or not doc_terms:
            return 50.0

        overlap = len(pr_terms & doc_terms) / len(pr_terms | doc_terms)
        if overlap <= low:
            return 65.0                  # little shared vocabulary → misaligned
        if overlap >= high:
            return 15.0                  # clearly on-scope → low (trusted)
        return linear(overlap, low, high, 65.0, 15.0)

    # -- LLM: judge alignment -------------------------------------------

    def _score_llm(self, docs: str) -> float:
        title = self.pr_data.get("title") or ""
        body = self.pr_data.get("body") or ""
        patch_context = self._patch_context()
        patch_section = (
            f"\nSelected PR patches (bounded; may be empty for binary/large files):\n{patch_context}\n"
            if patch_context
            else ""
        )
        prompt = (
            "You are scoring how well a pull request aligns with a project's\n"
            "documented scope, roadmap, and architecture.\n"
            "0 = clearly on-scope / fits the architecture, "
            "100 = conflicts with the roadmap / reshapes architecture "
            "without justification.\n\n"
            f"Project scope/roadmap/architecture docs (truncated):\n{docs[:4000]}\n\n"
            f"PR title: {title}\n"
            f"PR body (truncated):\n{body[:2000]}\n\n"
            f"{patch_section}"
            'Return ONLY a JSON object: {"score": <integer 0-100>, "reason": "<short>"}'
        )
        content = self.gh.llm_judge(prompt, system=LLM_JSON_SYSTEM)
        data = extract_first_json(content)
        return clamp_score(data.get("score"))

    def _patch_context(self) -> str:
        max_files, max_chars = patch_context_limits(self.config)
        try:
            files = self.gh.fetch_pr_files()
        except Exception as exc:
            print(f"⚠️  scope_alignment: fetch PR patches failed ({exc!r}); continuing without patches")
            return ""
        return patch_context_from_files(files, max_files=max_files, max_chars=max_chars)
