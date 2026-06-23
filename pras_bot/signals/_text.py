"""Shared text helpers for the title/body signals.

Kept in one place so ``duplicate_pr_titles`` and ``duplicate_work`` use the
*same* normalization, and ``related_work`` / ``scope_alignment`` use the same
tokenizer — no subtle drift between near-duplicate detectors.
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def normalize_title(title: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation → for similarity.

    Two PRs whose titles differ only in casing / punctuation / spacing map to
    the same key, so an exact-normalized match is a strong duplication signal.
    """
    title = title.lower()
    title = re.sub(r"[^\w\s]", " ", title)      # punctuation → space
    title = re.sub(r"\s+", " ", title).strip()
    return title


def tokenize(text: str) -> set[str]:
    """Lowercased alphanumeric tokens longer than 2 chars (for Jaccard overlap)."""
    return {tok for tok in _TOKEN_RE.findall(text.lower()) if len(tok) > 2}
