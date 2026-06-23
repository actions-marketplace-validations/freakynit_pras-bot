"""signal: files_changed — score based on number of modified files.

Rationale: Trivial spam PRs often touch exactly 1 file (drive-by).
Moderately legitimate PRs touch 2-10 files. PRs touching >10 files
with few lines each are suspicious (batch find-and-replace spam).
"""

from __future__ import annotations

from .base import ScoredSignal, linear


class FilesChangedSignal(ScoredSignal):
    def score(self) -> float:
        changed: int = self.pr_data.get("changed_files", 0)

        sig_cfg = self.config.get("signals", {}).get(self.name(), {})
        thresholds = sig_cfg.get("thresholds", {})
        low_max = thresholds.get("low_max", 3)
        med_max = thresholds.get("med_max", 10)

        if changed == 0:
            return 100.0

        if changed <= low_max:
            # 1-3 files → 40 down to 20
            return linear(changed, 1, low_max, 40, 20)
        elif changed <= med_max:
            # 4-10 files → 20 down to 10
            return linear(changed, low_max + 1, med_max, 20, 10)
        else:
            # > 10 files: 10 → 100 (steep rise)
            return min(100.0, 10 + (changed - med_max) * 6)
