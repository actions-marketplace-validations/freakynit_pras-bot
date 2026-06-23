"""Configuration loader — merges user config over defaults."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "default_config.yml"


def _fetch_repo_config(token: str, repo_full: str) -> dict[str, Any] | None:
    """Try to fetch `.github/pras-bot.yml` from the repo via the GitHub API."""
    import urllib.request
    import urllib.error

    url = f"https://api.github.com/repos/{repo_full}/contents/.github/pras-bot.yml"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github.v3+json")
    req.add_header("User-Agent", "pras-bot")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise RuntimeError(f"GitHub API error fetching config: {exc.code} {exc.reason}") from exc

    content = data.get("content", "")
    if not content:
        return None
    decoded = base64.b64decode(content.replace("\n", "")).decode("utf-8")
    return yaml.safe_load(decoded) or {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base*."""
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            base[key] = _deep_merge(base[key], val)
        else:
            base[key] = val
    return base


def load_config(
    local_path: str | None,
    token: str,
    repo_full: str,
) -> dict[str, Any]:
    """Load and merge configuration.

    Priority (highest wins):
      1. Local file passed via `INPUT_CONFIG_PATH` (for local testing)
      2. `.github/pras-bot.yml` fetched from the repo
      3. Built-in `config/default_config.yml`
    """
    # 1. base defaults
    with open(_DEFAULT_CONFIG_PATH, encoding="utf-8") as f:
        config: dict[str, Any] = yaml.safe_load(f) or {}

    # 2. repo-level config
    repo_config = _fetch_repo_config(token, repo_full)
    if repo_config:
        config = _deep_merge(config, repo_config)
        print("📄  merged repo-level config from .github/pras-bot.yml")

    # 3. local-test override
    if local_path:
        with open(local_path, encoding="utf-8") as f:
            local = yaml.safe_load(f) or {}
        config = _deep_merge(config, local)
        print(f"📄  applied local config override: {local_path}")

    return config
