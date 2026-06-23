"""Shared path helpers for the file-list based signals.

``matches_any`` matches a path against a list of glob patterns in two ways at
once so repo owners can write whichever is natural:

  * **full path** — ``.github/workflows/*`` matches
    ``.github/workflows/ci.yml`` (fnmatch ``*`` spans ``/``).
  * **path segment** — ``package.json`` matches ``pkg/package.json`` and
    ``migrations`` matches ``db/migrations/0001.sql``.

Segment matching keeps name patterns (``test_*.py``, ``Dockerfile``,
``vendor``) from spuriously matching substrings of unrelated names
(``latest_news.py`` is *not* a test file).
"""

from __future__ import annotations

import fnmatch


def matches_any(path: str, patterns: list[str]) -> bool:
    """True if *path* matches any glob in *patterns* (full path or segment)."""
    if not path or not patterns:
        return False
    low = path.lower()
    segments = low.split("/")
    for pat in patterns:
        if not pat:
            continue
        p = pat.lower()
        if fnmatch.fnmatch(low, p):
            return True
        for seg in segments:
            if fnmatch.fnmatch(seg, p):
                return True
    return False


def top_scope(path: str) -> str:
    """First path component; root files (no ``/``) collapse to a single ``.``."""
    if "/" in path:
        return path.split("/", 1)[0]
    return "."
