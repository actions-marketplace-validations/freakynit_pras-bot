"""GitHub API client — thin wrapper around the REST API."""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

GRAPHQL_URL = "https://api.github.com/graphql"
_SEARCH_URL = "https://api.github.com/search/issues"


class GitHubAPIError(RuntimeError):
    """Raised when a GitHub API call fails with an HTTP error.

    Subclasses ``RuntimeError`` so existing ``except RuntimeError`` handlers
    keep working, but exposes ``status_code`` so callers can branch on it
    (e.g. only treat 404 as "not found").
    """

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class GitHubClient:
    """Encapsulates GitHub REST & GraphQL calls for a single PR context."""

    def __init__(
        self,
        token: str,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        pr_node_id: str | None,
        llm_config: dict[str, Any] | None = None,
    ) -> None:
        self.token = token
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.pr_number = pr_number
        self.pr_node_id = pr_node_id
        self._rest_base = f"https://api.github.com/repos/{repo_owner}/{repo_name}"
        # Cached user profiles so multiple signals share one /users/:login call.
        self._user_cache: dict[str, dict[str, Any]] = {}
        self._repo_cache: dict[str, Any] | None = None
        # Cached repo files (contents/{path}) so several signals reading the
        # same CONTRIBUTING.md / template / roadmap don't refetch it.
        self._file_cache: dict[str, str | None] = {}
        # Cached PR file list / commits / repo PR titles — shared across the
        # path-based (repo-fit + maintainer-burden) signals.
        self._pr_files_cache: list[dict[str, Any]] | None = None
        self._pr_commits_cache: list[dict[str, Any]] | None = None
        self._repo_prs_cache: list[dict[str, Any]] | None = None
        self._file_commit_cache: dict[str, str] = {}
        # LLM config (GitHub Models) — only used by NLP signals when enabled.
        self.llm_config: dict[str, Any] = llm_config or {}

    # ------------------------------------------------------------------
    # REST helpers
    # ------------------------------------------------------------------

    def _rest_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "pras-bot",
        }

    def _rest_get(self, url: str, timeout: int = 15) -> dict[str, Any] | list[Any]:
        req = urllib.request.Request(url, headers=self._rest_headers())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            raise GitHubAPIError(exc.code, f"REST GET {url} → {exc.code} {exc.reason}: {body}") from exc

    def _rest_get_paginated(self, url: str, *, per_page: int = 100, timeout: int = 15) -> list[Any]:
        """Return every item from a simple page/per_page REST list endpoint."""
        items: list[Any] = []
        page = 1
        separator = "&" if "?" in url else "?"
        while True:
            page_url = f"{url}{separator}per_page={per_page}&page={page}"
            data = self._rest_get(page_url, timeout=timeout)
            if not isinstance(data, list):
                return items
            items.extend(data)
            if len(data) < per_page:
                return items
            page += 1

    def _rest_post(self, url: str, body: dict[str, Any], timeout: int = 15) -> dict[str, Any]:
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, headers=self._rest_headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode(errors="replace")
            raise GitHubAPIError(exc.code, f"REST POST {url} → {exc.code} {exc.reason}: {err_body}") from exc

    def _rest_delete(self, url: str, timeout: int = 15) -> None:
        req = urllib.request.Request(url, headers=self._rest_headers(), method="DELETE")
        try:
            with urllib.request.urlopen(req, timeout=timeout):
                pass
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode(errors="replace")
            raise GitHubAPIError(exc.code, f"REST DELETE {url} → {exc.code} {exc.reason}: {err_body}") from exc

    # ------------------------------------------------------------------
    # GraphQL helpers
    # ------------------------------------------------------------------

    def _gql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = json.dumps({"query": query, "variables": variables or {}}).encode()
        req = urllib.request.Request(
            GRAPHQL_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "pras-bot",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                result: dict[str, Any] = json.load(resp)
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode(errors="replace")
            raise RuntimeError(f"GraphQL error {exc.code}: {err_body}") from exc

        if "errors" in result:
            raise RuntimeError(f"GraphQL errors: {result['errors']}")
        return result["data"]

    # ------------------------------------------------------------------
    # Search helpers  (shared by several trust signals)
    # ------------------------------------------------------------------

    def _search_issues_count(self, query: str) -> int:
        """Return total_count for a search/issues query (1 cheap request)."""
        url = _SEARCH_URL + "?" + urllib.parse.urlencode(
            {"q": query, "per_page": "1"}, quote_via=urllib.parse.quote
        )
        data = self._rest_get(url)
        assert isinstance(data, dict)
        return int(data.get("total_count", 0))

    def _search_issues_items(self, query: str, limit: int = 30) -> list[dict[str, Any]]:
        """Return up to *limit* issue/PR items matching the query."""
        url = _SEARCH_URL + "?" + urllib.parse.urlencode(
            {"q": query, "per_page": str(limit)}, quote_via=urllib.parse.quote
        )
        data = self._rest_get(url)
        assert isinstance(data, dict)
        items = data.get("items", []) or []
        return [it for it in items if isinstance(it, dict)]

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def fetch_pr_details(self) -> dict[str, Any]:
        """Return the PR object from the REST API.

        Includes additions, deletions, changed_files, user, and
        author_association — so several signals need no extra API calls.
        """
        url = f"{self._rest_base}/pulls/{self.pr_number}"
        data = self._rest_get(url)
        assert isinstance(data, dict)
        return data

    def fetch_user_profile(self, username: str) -> dict[str, Any]:
        """Return the full user object (cached per client instance)."""
        if username in self._user_cache:
            return self._user_cache[username]
        url = f"https://api.github.com/users/{urllib.parse.quote(username, safe='')}"
        data = self._rest_get(url)
        assert isinstance(data, dict)
        self._user_cache[username] = data
        return data

    def fetch_user_created_at(self, username: str) -> str:
        """ISO-8601 created_at (reuses the cached user profile)."""
        return self.fetch_user_profile(username).get("created_at", "")

    def search_prs_by_author(self, author: str, lookback_days: int = 7, max_results: int = 20) -> int:
        """Count PRs opened by *author* across all public repos in the last
        *lookback_days* days. Returns the total_count.
        """
        since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        return self._search_issues_count(f"type:pr author:{author} created:>={since}")

    # -- counts used by the trust signals --------------------------------

    def count_merged_prs(self, author: str) -> int:
        """Merged PRs by *author* across all public repos."""
        return self._search_issues_count(f"type:pr author:{author} is:merged")

    def count_merged_prs_in_repo(self, author: str) -> int:
        """Merged PRs by *author* in *this* repo (prior accepted work)."""
        return self._search_issues_count(
            f"type:pr author:{author} repo:{self.repo_owner}/{self.repo_name} is:merged"
        )

    def count_closed_unmerged_prs(self, author: str) -> int:
        """Closed-without-merge PRs by *author* (rejected work)."""
        return self._search_issues_count(f"type:pr author:{author} is:closed is:unmerged")

    def count_authored_prs(self, author: str) -> int:
        """Total PRs authored by *author*."""
        return self._search_issues_count(f"type:pr author:{author}")

    def count_engaged_prs(self, author: str) -> int:
        """PRs authored by *author* where they also commented (engagement)."""
        return self._search_issues_count(f"type:pr author:{author} commenter:{author}")

    def count_issue_comments_in_repo(self, author: str) -> int:
        """Issues in *this* repo that *author* has commented on."""
        return self._search_issues_count(
            f"type:issue commenter:{author} repo:{self.repo_owner}/{self.repo_name}"
        )

    # -- item fetches ----------------------------------------------------

    def fetch_recent_pr_titles(self, author: str, limit: int = 30) -> list[str]:
        """Titles of the author's most recent PRs (for duplicate detection)."""
        items = self._search_issues_items(f"type:pr author:{author}", limit)
        return [it.get("title", "") for it in items]

    def fetch_recent_pr_activity(
        self, author: str, lookback_days: int = 30, limit: int = 30
    ) -> list[dict[str, Any]]:
        """Recent PRs by *author* with creation time + repo (for burstiness)."""
        since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        items = self._search_issues_items(
            f"type:pr author:{author} created:>={since}", limit
        )
        out: list[dict[str, Any]] = []
        for it in items:
            repo_url = it.get("repository_url", "")
            # repository_url looks like .../repos/{owner}/{repo}
            repo_full = repo_url.rsplit("/repos/", 1)[-1] if "/repos/" in repo_url else ""
            out.append({"created_at": it.get("created_at", ""), "repo": repo_full})
        return out

    # ------------------------------------------------------------------
    # LLM + repo-content helpers (optional — only used by NLP signals)
    # ------------------------------------------------------------------

    def fetch_repo_info(self) -> dict[str, Any]:
        """Return this repo's metadata (description, language, topics). Cached."""
        if self._repo_cache is None:
            data = self._rest_get(self._rest_base)   # GET /repos/{owner}/{repo}
            assert isinstance(data, dict)
            self._repo_cache = data
        return self._repo_cache

    def fetch_file_text(self, path: str) -> str | None:
        """Return decoded file content from the repo, or None if missing (404).

        Cached per path so multiple signals reading the same reference doc
        (CONTRIBUTING.md, PR template, ROADMAP.md, …) only hit the API once.
        """
        if path in self._file_cache:
            return self._file_cache[path]
        url = f"{self._rest_base}/contents/{urllib.parse.quote(path, safe='/')}"
        try:
            data = self._rest_get(url)
        except GitHubAPIError as exc:
            if exc.status_code == 404:
                self._file_cache[path] = None
                return None
            raise
        assert isinstance(data, dict)
        content = data.get("content", "") or ""
        if data.get("encoding") == "base64":
            text = base64.b64decode(content.replace("\n", "")).decode("utf-8", errors="replace")
        else:
            text = content
        self._file_cache[path] = text
        return text

    def fetch_pr_files(self) -> list[dict[str, Any]]:
        """Return the PR's changed files (filename, status, additions, …).

        Cached on the client so the several path-based signals
        (tests_included, change_scope, risky_paths, file_maintenance) share
        one paginated ``GET /pulls/{n}/files`` call sequence.
        """
        if self._pr_files_cache is None:
            data = self._rest_get_paginated(f"{self._rest_base}/pulls/{self.pr_number}/files")
            self._pr_files_cache = [item for item in data if isinstance(item, dict)]
        return self._pr_files_cache

    def fetch_pr_commits(self) -> list[dict[str, Any]]:
        """Return the PR's commits (with commit.message for DCO/sign-off checks).

        Cached on the client and paginated past GitHub's default 100-item page.
        """
        if self._pr_commits_cache is None:
            data = self._rest_get_paginated(f"{self._rest_base}/pulls/{self.pr_number}/commits")
            self._pr_commits_cache = [item for item in data if isinstance(item, dict)]
        return self._pr_commits_cache

    def fetch_repo_pr_titles(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent PRs in *this* repo with title/state/number.

        Used by ``duplicate_work`` to spot a PR that duplicates an existing
        one by title. Cached on the client.
        """
        if self._repo_prs_cache is None:
            items = self._search_issues_items(
                f"type:pr repo:{self.repo_owner}/{self.repo_name}", limit
            )
            self._repo_prs_cache = [
                {
                    "title": it.get("title", ""),
                    "state": it.get("state", ""),
                    "number": it.get("number"),
                }
                for it in items
                if isinstance(it, dict)
            ]
        return self._repo_prs_cache

    def fetch_file_last_commit_date(self, path: str) -> str:
        """Return ISO date of the most recent commit on the default branch that
        touched *path* ("" if the file has no prior history — i.e. newly added).

        Used by ``file_maintenance`` (only when ``check_recency`` is on) to tell
        actively-maintained files from stale ones. Cached per path.
        """
        if path in self._file_commit_cache:
            return self._file_commit_cache[path]
        url = (
            f"{self._rest_base}/commits?"
            + urllib.parse.urlencode({"path": path, "per_page": "1"})
        )
        data = self._rest_get(url)
        items = data if isinstance(data, list) else []
        date = ""
        if items:
            commit = items[0].get("commit", {}) or {}
            date = (
                (commit.get("author") or {}).get("date")
                or (commit.get("committer") or {}).get("date")
                or ""
            )
        self._file_commit_cache[path] = date
        return date

    def llm_judge(self, prompt: str, *, system: str | None = None) -> str:
        """Call GitHub Models (OpenAI-compatible) and return the assistant text.

        Requires the calling workflow to grant ``permissions: models: read``
        and ``llm.enabled: true`` in config. Raises ``GitHubAPIError`` on HTTP
        failure so signals can catch it and degrade to a neutral score.
        """
        cfg = self.llm_config
        endpoint = cfg.get("endpoint", "https://models.github.ai/inference/chat/completions")
        model = cfg.get("model", "openai/gpt-4o-mini")
        temperature = cfg.get("temperature", 0.0)
        max_tokens = cfg.get("max_tokens", 5000)
        max_input_tokens = cfg.get("max_input_tokens", 50000)
        timeout = cfg.get("timeout", 30)
        prompt = self._truncate_prompt(prompt, max_input_tokens=max_input_tokens)

        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = json.dumps({
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }).encode()

        req = urllib.request.Request(
            endpoint,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "pras-bot",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.load(resp)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            raise GitHubAPIError(exc.code, f"LLM judge → {exc.code} {exc.reason}: {body}") from exc

        assert isinstance(data, dict)
        choices = data.get("choices") or []
        if not choices:
            raise GitHubAPIError(500, f"LLM judge: empty choices in response: {data}")
        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, list):  # some models return content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        return content if isinstance(content, str) else str(content)

    @staticmethod
    def _truncate_prompt(prompt: str, *, max_input_tokens: Any) -> str:
        """Apply a rough input-token cap while preserving prompt instructions."""
        try:
            token_limit = int(max_input_tokens)
        except (TypeError, ValueError):
            token_limit = 50000
        if token_limit <= 0:
            return prompt

        max_chars = token_limit * 4
        if len(prompt) <= max_chars:
            return prompt

        marker = "\n\n[...truncated to llm.max_input_tokens budget...]\n\n"
        keep = max_chars - len(marker)
        if keep <= 0:
            return prompt[:max_chars]
        head = keep // 2
        tail = keep - head
        return prompt[:head] + marker + prompt[-tail:]

    # ------------------------------------------------------------------
    # Labelling
    # ------------------------------------------------------------------

    def apply_labels(
        self,
        chosen: dict[str, str] | None,
        all_labels: list[dict[str, str]],
    ) -> None:
        """Apply *chosen* label to the PR, then strip every other bot-managed
        label so exactly one pras-bot label is ever present at a time.

        *chosen*     – the single label dict to apply (or ``None`` if no
                       threshold matches; in that case all bot labels are removed).
        *all_labels* – every label defined in the config; used to know which
                       labels this bot owns and must clean up when stale.
        """
        bot_names = {lab["name"] for lab in all_labels}
        keep: str | None = None

        if chosen is not None:
            keep = chosen["name"]
            self._ensure_label_exists(chosen)
            self._rest_post(
                f"{self._rest_base}/issues/{self.pr_number}/labels",
                {"labels": [keep]},
            )
            print(f"🏷️  applied label: {keep}")

        # Remove any other bot-managed labels left over from previous runs.
        current_labels = self._rest_get(
            f"{self._rest_base}/issues/{self.pr_number}/labels"
        )
        assert isinstance(current_labels, list)
        for lbl in current_labels:
            assert isinstance(lbl, dict)
            name = lbl.get("name", "")
            if name in bot_names and name != keep:
                self._rest_delete(
                    f"{self._rest_base}/issues/{self.pr_number}/labels/{urllib.parse.quote(name, safe='')}"
                )
                print(f"🧹  removed stale label: {name}")

    def _ensure_label_exists(self, label: dict[str, str]) -> None:
        """Create the label in the repo if it doesn't already exist."""
        name = label["name"]
        color = label.get("color", "ededed")
        description = label.get("description", "")
        try:
            self._rest_get(f"{self._rest_base}/labels/{urllib.parse.quote(name, safe='')}")
        except GitHubAPIError as exc:
            # 404 → label doesn't exist yet, create it. Anything else
            # (rate limit, 500, …) is a real error and must propagate.
            if exc.status_code != 404:
                raise
            self._rest_post(
                f"{self._rest_base}/labels",
                {"name": name, "color": color, "description": description},
            )
            print(f"🏷️  created label: {name} (#{color})")

    def add_comment(
        self,
        score: float,
        breakdown: list[dict[str, Any]],
        chosen: dict[str, str] | None,
    ) -> None:
        """Post a scorecard comment on the PR."""
        lines = [
            "## 🤖 PR Anti-Spam Bot Scorecard",
            "",
            f"**Spam Score:** `{score:.1f}` / 100",
            "",
            "| Signal                   | Raw Score | Weight | Weighted |",
            "|--------------------------|-----------|--------|----------|",
        ]
        for s in breakdown:
            if s.get("raw") is None:
                lines.append(
                    f"| {s['name']:24s} |    —     | {s['weight']:5.2f}  |    —     |  skipped"
                )
            else:
                lines.append(
                    f"| {s['name']:24s} | {s['raw']:8.1f} | {s['weight']:5.2f}  | {s['weighted']:7.1f} |"
                )

        applied = chosen["name"] if chosen else None
        lines.append("")
        lines.append(f"**Applied label:** `{applied}`" if applied else "**No label applied**")
        lines.append("")
        lines.append(
            "> Configure thresholds & weights via [`.github/pras-bot.yml`]"
            f"(https://github.com/{self.repo_owner}/{self.repo_name}/blob/main/.github/pras-bot.yml)."
        )

        self._rest_post(
            f"{self._rest_base}/issues/{self.pr_number}/comments",
            {"body": "\n".join(lines)},
        )
        print("💬  scorecard comment posted")
