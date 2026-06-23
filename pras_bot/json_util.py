"""Utilities for parsing JSON out of LLM responses.

LLMs are asked to return *only* JSON, but in practice they often wrap it in
markdown fences (```json ... ```) or add a sentence of prose. ``extract_first_json``
finds the first balanced JSON object/array regardless of surrounding text, so
the caller never has to trust the model's formatting.
"""

from __future__ import annotations

import json
from typing import Any


def extract_first_json(text: str) -> Any:
    """Find and parse the first balanced JSON object/array in *text*.

    Scans for the first ``{`` or ``[`` and matches braces/brackets (respecting
    string literals and ``\\`` escapes) to find the closing delimiter, then
    parses. If that fails it retries from the next opening bracket — so it
    copes with prose, markdown fences, and partial preamble all at once.

    Raises ``ValueError`` if no valid JSON block is found.
    """
    if not text:
        raise ValueError("empty text — no JSON to parse")

    last_err: Exception | None = None
    for i, ch in enumerate(text):
        if ch not in "{[":
            continue
        end = _scan_balanced(text, i)
        if end is None:
            continue
        try:
            return json.loads(text[i : end + 1])
        except json.JSONDecodeError as exc:
            last_err = exc
            continue

    raise ValueError(f"no valid JSON object/array found in response: {last_err}")


def _scan_balanced(text: str, start: int) -> int | None:
    """Return the index of the bracket that closes the one at *start*.

    Tracks depth of the opener type only (``{``/``}`` or ``[``/``]``) while
    skipping over string literals and escapes, so brackets inside strings or
    of the other type don't confuse the scan.
    """
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return i
    return None
