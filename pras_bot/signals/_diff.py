"""Helpers for bounded PR patch context in LLM signals."""

from __future__ import annotations

from typing import Any


def patch_context_from_files(
    files: list[dict[str, Any]],
    *,
    max_files: int = 3,
    max_chars: int = 5000,
) -> str:
    """Return a compact diff context from the largest changed files.

    GitHub's PR files API includes a ``patch`` field for text files. We pass
    only a small, addition-sorted subset to LLM signals so prompts get the
    signal-rich changed lines without sending every file in a large PR.
    """
    if max_files <= 0 or max_chars <= 0:
        return ""

    candidates = [f for f in files if isinstance(f.get("patch"), str) and f.get("patch")]
    if not candidates:
        return ""

    if len(candidates) <= max_files:
        selected = candidates
    else:
        selected = sorted(candidates, key=lambda f: int(f.get("additions") or 0), reverse=True)[:max_files]

    chunks: list[str] = []
    remaining = max_chars
    for file_info in selected:
        filename = file_info.get("filename") or "(unknown file)"
        status = file_info.get("status") or "modified"
        additions = int(file_info.get("additions") or 0)
        deletions = int(file_info.get("deletions") or 0)
        header = f"File: {filename} ({status}, +{additions}/-{deletions})\n"
        patch = file_info.get("patch") or ""
        block_prefix = header + "```diff\n"
        block_suffix = "\n```"
        overhead = len(block_prefix) + len(block_suffix)
        if remaining <= overhead:
            break
        patch_budget = remaining - overhead
        patch_text = patch[:patch_budget]
        truncated = len(patch) > patch_budget
        if truncated and patch_budget > 32:
            patch_text = patch_text[:-32] + "\n[patch truncated]"
        chunk = block_prefix + patch_text + block_suffix
        chunks.append(chunk)
        remaining -= len(chunk) + 2
        if remaining <= 0:
            break

    return "\n\n".join(chunks)


def patch_context_limits(config: dict[str, Any]) -> tuple[int, int]:
    """Read global LLM patch-context limits from config."""
    llm_cfg = config.get("llm", {})
    patch_cfg = llm_cfg.get("patch_context", {}) or {}
    try:
        max_files = int(patch_cfg.get("max_files", 3))
    except (TypeError, ValueError):
        max_files = 3
    try:
        max_chars = int(patch_cfg.get("max_chars", 5000))
    except (TypeError, ValueError):
        max_chars = 5000
    return max_files, max_chars
