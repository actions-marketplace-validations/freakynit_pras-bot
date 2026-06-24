"""signal: tests_included — does the PR include test changes?

Rationale: Spam / low-quality PRs that change source code almost never add
tests. A PR that adds tests alongside code (or is tests-only) is a positive
signal. Docs/config-only PRs don't need tests and are skipped.

Uses the PR's changed-file list (one shared ``GET /pulls/{n}/files`` call,
cached for the other path-based signals).

Covers:
  * "No tests are included"          (negative — increases burden)
  * "Tests included"                 (positive — decreases burden)
"""

from __future__ import annotations

from .base import ScoredSignal, linear
from ._paths import matches_any

# Default test-file / test-dir patterns. Matched by full path OR path segment.
_DEFAULT_TEST_PATTERNS = [
    # Python
    "test_*.py",            # test_foo.py
    "*_test.py",            # foo_test.py
    "test.py",
    "conftest.py",

    # JS/TS
    "*.test.js", "*.test.ts", "*.test.jsx", "*.test.tsx",
    "*.spec.js", "*.spec.ts", "*.spec.jsx", "*.spec.tsx",
    "__tests__",
    "cypress",
    "playwright",

    # Go
    "*_test.go",

    # Rust
    "*_test.rs",

    # JVM (Java/Kotlin/Scala)
    "*Test.java", "*Tests.java", "*TestCase.java", "*IT.java",
    "*Test.kt", "*Tests.kt",
    "*Test.scala", "*Spec.scala",
    "*.test.kts",

    # .NET
    "*Test.cs", "*Tests.cs", "*.Tests.cs",

    # Ruby
    "*_spec.rb", "*_test.rb", "spec_helper.rb",

    # PHP
    "*Test.php", "*_test.php",

    # C/C++
    "*_test.cc", "*_test.cpp", "*Test.cpp", "test_*.cc", "test_*.cpp",

    # Swift
    "*Tests.swift", "*Test.swift",

    # Elixir
    "*_test.exs",

    # Haskell
    "*Spec.hs",

    # Dart/Flutter
    "*_test.dart",

    # Shell
    "*.bats",
    "*_test.sh",

    # BDD/Gherkin (cucumber, behave, etc.)
    "*.feature",

    # Generic dirs/segments
    "tests",
    "test",
    "testing",
    "spec",
    "specs",
    "e2e",
    "e2e-tests",
    "integration",
    "integration-tests",
    "unit-tests",
]


class TestsIncludedSignal(ScoredSignal):
    # Tell pytest this isn't a test class (its name just starts with "Test").
    __test__ = False

    def score(self) -> float | None:
        try:
            files = self.gh.fetch_pr_files()
        except Exception as exc:
            print(f"⚠️  tests_included: fetch files failed ({exc!r}); using neutral score")
            return 50.0
        if not files:
            return 50.0

        sig_cfg = self.config.get("signals", {}).get(self.name(), {})
        # empty list → built-in defaults
        test_patterns: list[str] = sig_cfg.get("test_patterns") or _DEFAULT_TEST_PATTERNS
        small_max = sig_cfg.get("small_max_lines", 50)
        med_max = sig_cfg.get("med_max_lines", 300)

        test_files = [f for f in files if matches_any(f.get("filename", ""), test_patterns)]
        non_test_files = [f for f in files if not matches_any(f.get("filename", ""), test_patterns)]

        # Docs / config / lockfile-only PR (no non-test code) → tests N/A.
        if not non_test_files:
            return 5.0                       # tests-only PR → strongly positive

        if test_files:
            return 15.0                     # code + tests → positive

        # Code change with no tests → suspicion scales with change size.
        size = int(self.pr_data.get("additions", 0)) + int(self.pr_data.get("deletions", 0))
        if size <= small_max:
            return 40.0
        if size <= med_max:
            return linear(size, small_max, med_max, 40.0, 60.0)
        return min(100.0, 60.0 + (size - med_max) * 0.05)
