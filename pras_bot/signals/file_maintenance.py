"""signal: file_maintenance — does the PR touch unmaintained / generated /
vendored files unnecessarily?

Rationale: Touching deprecated, archived, generated, or vendored files is a
negative signal (low value, high review burden, often bot-generated churn).
Touching actively-maintained source files is positive.

Two checks, both configurable:

  * **skip patterns** (always on) — files under ``vendor/``, ``node_modules/``,
    ``dist/``, ``*.generated.*``, ``*.pb.go``, etc. The score rises with the
    share of changed files that match.
  * **recency** (opt-in, ``check_recency: true``) — for up to ``max_files``
    real-source files, fetch the last commit that touched each on the default
    branch; files untouched for > ``stale_days`` are "stale" and bump the
    score. Newly-added files (no prior history) are treated as maintained.

Uses the PR's changed-file list (shared ``GET /pulls/{n}/files`` call); the
recency check adds up to ``max_files`` ``GET /commits?path=`` calls.

Covers:
  * "Touches deprecated, archived, generated, or vendored files unnecessarily"
  * "Touches actively maintained files"
"""

from __future__ import annotations

from datetime import datetime, timezone

from .base import ScoredSignal, linear
from ._paths import matches_any

# Default vendored / generated / archived / deprecated path patterns.
_DEFAULT_SKIP_PATTERNS = [
    "vendor",
    "vendors",
    "third_party",
    "third-party",
    "node_modules",
    "bower_components",
    "jspm_packages",
    "dist",
    "build",
    "out",
    "target",
    "*.generated.*",
    "*.gen.*",
    "*_generated.*",
    "*.pb.go",
    "*.pb.cc",
    "*.pb.h",
    "*.g.dart",
    "*.min.js",
    "*.min.css",
    "*.map",
    "zz_*.*",               # deprecated Go files (compiler convention)
    "*.pb.swift",
    "Pods",
]


class FileMaintenanceSignal(ScoredSignal):
    def score(self) -> float:
        try:
            files = self.gh.fetch_pr_files()
        except Exception as exc:
            print(f"⚠️  file_maintenance: fetch files failed ({exc!r}); using neutral score")
            return 50.0
        if not files:
            return 50.0

        sig_cfg = self.config.get("signals", {}).get(self.name(), {})
        # empty list → built-in defaults
        skip_patterns: list[str] = sig_cfg.get("skip_patterns") or _DEFAULT_SKIP_PATTERNS
        thresholds = sig_cfg.get("thresholds", {})
        low_max = thresholds.get("low_max", 0.2)
        med_max = thresholds.get("med_max", 0.5)

        filenames = [f.get("filename", "") for f in files]
        skip_count = sum(1 for fn in filenames if matches_any(fn, skip_patterns))
        ratio = skip_count / len(files)

        if skip_count == 0:
            base = 5.0                      # touching real source → good
        elif ratio <= low_max:
            base = linear(ratio, 0.0, low_max, 5.0, 25.0)
        elif ratio <= med_max:
            base = linear(ratio, low_max, med_max, 25.0, 55.0)
        else:
            base = min(100.0, 55.0 + (ratio - med_max) * 80.0)

        if not sig_cfg.get("check_recency", False):
            return base

        return min(100.0, base + self._recency_penalty(filenames, skip_patterns, sig_cfg))

    # -- optional active-maintenance recency check -----------------------

    def _recency_penalty(self, filenames: list[str], skip_patterns: list[str], sig_cfg: dict) -> float:
        """0..40 boost based on how many touched source files are stale."""
        stale_days = int(sig_cfg.get("stale_days", 365))
        max_files = int(sig_cfg.get("max_files", 5))

        # Only inspect real source files (not vendored/generated), capped.
        candidates = [fn for fn in filenames if fn and not matches_any(fn, skip_patterns)][:max_files]
        if not candidates:
            return 0.0

        stale = 0
        checked = 0
        for fn in candidates:
            try:
                date_str = self.gh.fetch_file_last_commit_date(fn)
            except Exception as exc:
                print(f"⚠️  file_maintenance: recency lookup failed for {fn!r} ({exc!r})")
                continue
            checked += 1
            if not date_str:
                continue                    # newly added file → not stale
            try:
                last = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            age_days = (datetime.now(timezone.utc) - last).days
            if age_days > stale_days:
                stale += 1

        if checked == 0:
            return 0.0
        stale_ratio = stale / checked
        return 40.0 * stale_ratio
