"""signal: risky_paths — does the PR touch risky / critical paths?

Rationale: Changes to public API surface, database migrations, dependency
manifests/lockfiles, CI/build/deploy pipelines, or security/auth/payment/
networking code carry higher review burden and risk. Touching several such
areas in one PR is a strong negative (review-burden) signal.

Patterns are grouped (config: ``groups`` → name → list of globs). The score
scales with the *number of distinct risky groups* touched, so a PR that only
touches ``package.json`` is mildly risky while one that touches migrations +
deps + CI is highly risky. ``matches_any`` accepts both full-path globs
(``.github/workflows/*``) and segment names (``package.json``, ``migrations``).

Uses the PR's changed-file list (shared ``GET /pulls/{n}/files`` call).

Covers:
  * "Public API changes"
  * "Database migrations"
  * "Dependency changes"
  * "Build, CI, deployment, auth, security, payment, or networking changes"
"""

from __future__ import annotations

from .base import ScoredSignal, linear
from ._paths import matches_any

# Default risky-path groups. Repo owners can override / extend via config.
_DEFAULT_GROUPS: dict[str, list[str]] = {
    "public_api": [
        "api",
        "public",
        "__init__.py",
        "index.ts",
        "index.js",
        "index.d.ts",
        "mod.rs",
        "lib.rs",
        "exports.*",
        "barrel.*",
        "schema.*",
        "*.schema.json",
        "*.proto",
        "openapi.*",
        "swagger.*",
    ],
    "migrations": [
        "migrations",
        "migration",
        "db",
        "alembic",
        "flyway",
        "*.sql",
        "*.migration.*",
    ],
    "dependencies": [
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "requirements*.txt",
        "Pipfile",
        "Pipfile.lock",
        "poetry.lock",
        "uv.lock",
        "setup.py",
        "setup.cfg",
        "pyproject.toml",
        "Cargo.toml",
        "Cargo.lock",
        "go.mod",
        "go.sum",
        "pom.xml",
        "build.gradle*",
        "build.gradle.kts",
        "Gemfile",
        "Gemfile.lock",
        "composer.json",
        "composer.lock",
        "mix.lock",
        "*.cabal",
        "Project.toml",
        "deps.*",
    ],
    "ci_build_deploy": [
        ".github/workflows",
        "workflows",
        "Dockerfile*",
        "docker-compose*",
        "dockerfile*",
        ".dockerignore",
        "Jenkinsfile*",
        ".gitlab-ci.yml",
        "azure-pipelines*",
        ".circleci",
        "Makefile",
        "CMakeLists.txt",
        "tsconfig.json",
        "webpack*",
        "vite.config*",
        "rollup.config*",
        "babel.config*",
        ".eslintrc*",
        ".prettierrc*",
        "rust-toolchain*",
        ".terraform*",
        "*.tf",
        "helm",
        "kustomization*",
        "deploy*",
    ],
    "security_auth": [
        "auth",
        "security",
        "crypto",
        "permissions",
        "acl",
        "authorization",
        "authentication",
        "*password*",
        "*secret*",
        "*token*",
        "*jwt*",
        "*oauth*",
        "*saml*",
        "*credential*",
        "*crypt*",
    ],
    "payment": [
        "payment*",
        "billing",
        "checkout",
        "stripe*",
        "*invoice*",
        "subscription*",
    ],
    "networking": [
        "network*",
        "proxy",
        "gateway",
        "server",
        "middleware",
        "ingress",
        "loadbalancer*",
        "dns",
        "cdn",
        "socket*",
        "websocket*",
    ],
}


class RiskyPathsSignal(ScoredSignal):
    def score(self) -> float:
        try:
            files = self.gh.fetch_pr_files()
        except Exception as exc:
            print(f"⚠️  risky_paths: fetch files failed ({exc!r}); using neutral score")
            return 50.0
        if not files:
            return 50.0

        sig_cfg = self.config.get("signals", {}).get(self.name(), {})
        groups: dict[str, list[str]] = sig_cfg.get("groups", _DEFAULT_GROUPS)
        thresholds = sig_cfg.get("thresholds", {})
        low_max = thresholds.get("low_max", 0)
        med_max = thresholds.get("med_max", 2)
        high_max = thresholds.get("high_max", 3)

        touched: set[str] = set()
        for name, patterns in groups.items():
            if any(matches_any(f.get("filename", ""), patterns) for f in files):
                touched.add(name)
        n = len(touched)

        if n <= low_max:
            return 5.0                        # no risky area → low burden
        if n <= med_max:
            return linear(n, low_max, med_max, 5.0, 55.0)
        if n <= high_max:
            return linear(n, med_max, high_max, 55.0, 75.0)
        return min(100.0, 75.0 + (n - high_max) * 8.0)
