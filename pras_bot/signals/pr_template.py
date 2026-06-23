"""signal: pr_template — does the PR complete the repo's PR template?

Rationale: A PR that copies the template but leaves its fill-in fields blank
(empty body, leftover ``${VARIABLE}`` placeholders, missing sections) is a
mild negative signal — typical of drive-by / low-effort PRs.

Reference doc: the PR template file (config: ``template_path``), relative to
the repo root. Repo owners author the template with explicit ``${VARIABLE}``
placeholders (e.g. ``${SUMMARY}``, ``${WHY}``, ``${TESTING}``) instead of
HTML comments; the bot checks whether the PR body still contains those
placeholders literally (unfilled) or removed them (filled).

Provider choice (config: ``signals.pr_template.provider``):
  off      – signal disabled (default)
  non_llm  – pure-Python: count unfilled ``${VARIABLE}`` placeholders and
             missing section headers in the PR body. No cost.
  llm      – GitHub Models judges how well the template was completed.

Covers:
  * "Completes the PR template properly"      (positive)
  * "Ignores PR template"                     (negative)
"""

from __future__ import annotations

import re

from .base import LLM_JSON_SYSTEM, ScoredSignal, clamp_score, linear
from ..json_util import extract_first_json

# ${NAME} style placeholders — the explicit, easily-detected alternative to
# GitHub's default HTML-comment template fields.
_PLACEHOLDER_RE = re.compile(r"\$\{([^}]+)\}")
# Markdown ATX section headers (## Summary, ### Why, …).
_HEADER_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)\s*$", re.MULTILINE)


class PrTemplateSignal(ScoredSignal):
    def score(self) -> float | None:
        provider = self._resolve_provider()
        if provider == "off":
            return None

        sig_cfg = self.config.get("signals", {}).get(self.name(), {})
        template_path = sig_cfg.get("template_path", ".github/PULL_REQUEST_TEMPLATE.md")

        try:
            template = self.gh.fetch_file_text(template_path)
        except Exception as exc:
            print(f"⚠️  pr_template: fetch template failed ({exc!r}); using neutral score")
            return 50.0
        if not template:
            # No template in the repo → nothing to check → skip.
            return None

        try:
            if provider == "non_llm":
                return self._score_non_llm(template)
            return self._score_llm(template)
        except Exception as exc:
            print(f"⚠️  pr_template: failed ({exc!r}); using neutral score")
            return 50.0

    # -- non-LLM: unfilled placeholders + missing sections ------------------

    def _score_non_llm(self, template: str) -> float:
        body: str = self.pr_data.get("body") or ""
        if not body.strip():
            return 80.0                       # empty body → strongly unfilled

        placeholders = set(_PLACEHOLDER_RE.findall(template))
        headers = [h.strip() for h in _HEADER_RE.findall(template) if h.strip()]

        if placeholders:
            unfilled = [p for p in placeholders if f"${{{p}}}" in body]
            ratio = len(unfilled) / len(placeholders)
            score = linear(ratio, 0.0, 1.0, 10.0, 80.0)
            # All placeholders look filled, but if the body doesn't carry any
            # of the template's section headers either, the author probably
            # didn't use the template at all → nudge toward medium.
            if not unfilled and headers and not self._any_header_present(body, headers):
                score = max(score, 40.0)
            return score

        if headers:
            present = [h for h in headers if h.lower() in body.lower()]
            missing_ratio = (len(headers) - len(present)) / len(headers)
            return linear(missing_ratio, 0.0, 1.0, 10.0, 60.0)

        # Template is just prose with no placeholders / headers → can't judge
        # completion structurally → skip so we don't bias the score.
        return None

    @staticmethod
    def _any_header_present(body: str, headers: list[str]) -> bool:
        low = body.lower()
        return any(h.lower() in low for h in headers)

    # -- LLM: judge completion --------------------------------------------

    def _score_llm(self, template: str) -> float:
        title = self.pr_data.get("title") or ""
        body = self.pr_data.get("body") or ""
        prompt = (
            "You are scoring how completely a pull request fills in the\n"
            "repository's PR template. 0 = fully completed, 100 = ignores it\n"
            "(empty body, leftover ${VARIABLE} placeholders, missing sections).\n\n"
            f"PR template (truncated):\n{template[:3000]}\n\n"
            f"PR title: {title}\n"
            f"PR body (truncated):\n{body[:3000]}\n\n"
            'Return ONLY a JSON object: {"score": <integer 0-100>, "reason": "<short>"}'
        )
        content = self.gh.llm_judge(prompt, system=LLM_JSON_SYSTEM)
        data = extract_first_json(content)
        return clamp_score(data.get("score"))
