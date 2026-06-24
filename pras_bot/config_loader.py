"""Configuration loader — merges user config over defaults."""

from __future__ import annotations

import base64
import copy
import json
from pathlib import Path
from typing import Any

import yaml

from .github_client import _expect_dict, _github_urlopen_with_retries

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "default_config.yml"


def _fetch_repo_config(token: str, repo_full: str) -> dict[str, Any] | None:
    """Try to fetch `.github/pras-bot.yml` from the repo via the GitHub API."""
    import urllib.error
    import urllib.request

    url = f"https://api.github.com/repos/{repo_full}/contents/.github/pras-bot.yml"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github.v3+json")
    req.add_header("User-Agent", "pras-bot")

    try:
        with _github_urlopen_with_retries(req, timeout=10, context=f"REST GET {url}") as resp:
            data = _expect_dict(json.load(resp), f"REST GET {url}")
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
    """Recursively merge *override* over *base* without mutating inputs.

    Dicts are merged recursively. Lists and scalar values are replaced as whole
    values so repo configs can define exact ordered lists.
    """
    merged = copy.deepcopy(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = copy.deepcopy(val)
    return merged


def load_config(
    local_path: str | None,
    token: str,
    repo_full: str,
) -> dict[str, Any]:
    """Load and merge configuration.

    Built-in defaults are always loaded first. Then exactly one repo-level
    config source is applied:
      * explicit local file path from `INPUT_CONFIG_PATH`, or
      * `.github/pras-bot.yml` fetched from the repo when no local path is set.
    """
    # 1. base defaults
    with open(_DEFAULT_CONFIG_PATH, encoding="utf-8") as f:
        config: dict[str, Any] = yaml.safe_load(f) or {}

    if local_path:
        with open(local_path, encoding="utf-8") as f:
            local = yaml.safe_load(f) or {}
        config = _deep_merge(config, local)
        print(f"📄  applied config from local path: {local_path}")
    else:
        repo_config = _fetch_repo_config(token, repo_full)
        if repo_config:
            config = _deep_merge(config, repo_config)
            print("📄  merged repo-level config from .github/pras-bot.yml")

    return config
