"""signal: contribution_rules — does the PR follow the repo's contribution rules?

Rationale: A PR that ignores ``CONTRIBUTING.md`` / the PR template (missing
description, no tests, wrong format, …) is a mild negative signal.

Provider choice (config: ``signals.contribution_rules.provider``):
  off   – signal disabled (default)
  llm   – GitHub Models judges adherence against ``CONTRIBUTING.md`` (or the
          PR template). Costs money/quota.

  ``non_llm`` is **not** supported — interpreting free-form rules needs an
  LLM. If set, the signal is skipped with a warning.

Covers:
  * "Repeatedly ignores project contribution rules"
"""

from __future__ import annotations

from ._diff import patch_context_from_files, patch_context_limits
from .base import LLM_JSON_SYSTEM, ScoredSignal, clamp_score
from ..json_util import extract_first_json


class ContributionRulesSignal(ScoredSignal):
    def score(self) -> float | None:
        provider = self._resolve_provider()
        if provider == "off":
            return None
        if provider != "llm":
            print("⚠️  contribution_rules: only provider 'llm' is supported; skipping")
            return None

        try:
            return self._score_llm()
        except Exception as exc:
            print(f"⚠️  contribution_rules: failed ({exc!r}); using neutral score")
            return 50.0

    def _score_llm(self) -> float | None:
        sig_cfg = self.config.get("signals", {}).get(self.name(), {})
        contributing_path = sig_cfg.get("contributing_path", "CONTRIBUTING.md")
        template_path = sig_cfg.get("template_path", ".github/PULL_REQUEST_TEMPLATE.md")

        rules = self.gh.fetch_file_text(contributing_path) or ""
        if not rules:
            rules = self.gh.fetch_file_text(template_path) or ""
        if not rules:
            # No contribution rules in the repo → nothing to check → skip.
            return None

        title = self.pr_data.get("title") or ""
        body = self.pr_data.get("body") or ""
        patch_context = self._patch_context()
        patch_section = (
            f"\nSelected PR patches (bounded; may be empty for binary/large files):\n{patch_context}\n"
            if patch_context
            else ""
        )
        prompt = (
            "You are scoring how well a pull request follows a repository's\n"
            "contribution guidelines. 0 = fully compliant, 100 = ignores rules.\n\n"
            f"CONTRIBUTING guidelines (truncated):\n{rules[:4000]}\n\n"
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
            print(f"⚠️  contribution_rules: fetch PR patches failed ({exc!r}); continuing without patches")
            return ""
        return patch_context_from_files(files, max_files=max_files, max_chars=max_chars)
