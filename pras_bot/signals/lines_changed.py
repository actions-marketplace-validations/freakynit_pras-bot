"""signal: lines_changed — score based on how many lines were added+deleted.

Rationale: Auto-generated / trivial PRs tend to be either extremely tiny
(cosmetic) or suspiciously huge (entire file dumps). Normal PRs cluster in
a middle range.
"""

from __future__ import annotations

from .base import ScoredSignal, linear


class LinesChangedSignal(ScoredSignal):
    def score(self) -> float:
        additions: int = self.pr_data.get("additions", 0)
        deletions: int = self.pr_data.get("deletions", 0)
        total = additions + deletions

        sig_cfg = self.config.get("signals", {}).get(self.name(), {})
        thresholds = sig_cfg.get("thresholds", {})
        very_tiny = thresholds.get("very_tiny_max", 10)
        tiny = thresholds.get("tiny_max", 50)
        normal = thresholds.get("normal_max", 300)
        large = thresholds.get("large_max", 800)

        if total == 0:
            return 100.0                     # empty PR → max spam

        if total <= very_tiny:
            # 1-10 lines: linear from 100 (1 line) → 60 (10 lines)
            return linear(total, 1, very_tiny, 100, 60)
        elif total <= tiny:
            # 11-50 lines: 60 → 30
            return linear(total, very_tiny + 1, tiny, 60, 30)
        elif total <= normal:
            # 51-300: 30 → 10
            return linear(total, tiny + 1, normal, 30, 10)
        elif total <= large:
            # 301-800: 10 → 60 (creeps up for disproportionately large PRs)
            return linear(total, normal + 1, large, 10, 60)
        else:
            # > 800: 60 → 100
            return min(100.0, 60 + (total - large) * 0.1)
