# Signals Reference

PRAS Bot scores every PR on a **0–100 spam scale** (0 = clearly legitimate,
100 = almost certainly spam) by combining **24 weighted signals**, then maps
the final score to a single label.

## How the final score is computed

1. Each signal returns a **raw score** ∈ [0, 100] (higher = more suspicious).
2. **Weighted contribution** = `raw score × weight`.
3. **Final score** = `Σ(weighted) ÷ Σ(weights)`, clamped to [0, 100].
4. The final score is mapped to a label (checked top-down; one label only).

All weights and thresholds live in
[`config/default_config.yml`](config/default_config.yml) and can be
overridden per-repo via `.github/pras-bot.yml`.

> **Resilience:** every signal that calls the GitHub API degrades to a
> neutral **50** on failure (rate limit, 5xx, network error) instead of
> crashing the whole run. Unknown / undeterminable inputs also return 50.

## Signal overview

When all signals are enabled the total weight = **21.5**; the five LLM
signals are **off by default** and `signoff` is **opt-in**, so the default
effective total is **17.75**.
*Share* = `weight ÷ 21.5` (roughly how much each signal moves the final
score when all are on). *API* = extra GitHub API calls per PR run.

| Signal                  | Category           | Weight | Share  | What it measures                                          | API calls |
|-------------------------|--------------------|:------:|:------:|-----------------------------------------------------------|:---------:|
| `lines_changed`         | PR-shape           | 1.0    | 4.7%   | Added + deleted lines (too few *or* too many)            | 0         |
| `files_changed`         | PR-shape           | 1.0    | 4.7%   | Number of modified files (single-file drive-by)           | 0         |
| `account_age`           | PR-shape           | 1.5    | 7.0%   | How old the author's GitHub account is                    | 1 †       |
| `cross_repo_prs`        | PR-shape           | 2.0    | 9.3%   | PRs by the author across all repos in last 7 days         | 1 search  |
| `association`           | Contributor-trust  | 1.5    | 7.0%   | Author's repo role (owner/member/collaborator vs first-timer) | 0      |
| `repo_merge_history`    | Contributor-trust  | 1.5    | 7.0%   | Previously **merged** PRs by the author in this repo      | 1 search  |
| `closed_unmerged_ratio` | Contributor-trust  | 1.0    | 4.7%   | Share of the author's PRs closed *without* merge          | 2 search  |
| `issue_participation`   | Contributor-trust  | 0.75   | 3.5%   | Issues in this repo the author has commented on           | 1 search  |
| `review_engagement`     | Contributor-trust  | 0.75   | 3.5%   | Share of the author's own PRs where they replied           | 2 search  |
| `duplicate_pr_titles`   | Contributor-trust  | 1.25   | 5.8%   | How many of the author's recent PR titles are near-identical | 1 search |
| `bio_positioning`       | Contributor-trust  | 0.75   | 3.5%   | Generic "open source contributor" bio + little accepted work | 1 search †|
| `activity_burstiness`   | Contributor-trust  | 1.0    | 4.7%   | PRs clustered in a short window across many repos         | 1 search  |
| `tests_included`        | Repo-fit/Burden    | 0.75   | 3.5%   | Does the PR add tests alongside code?                     | 1 file †† |
| `change_scope`          | Repo-fit/Burden    | 0.75   | 3.5%   | Sprawl across unrelated top-level directories             | 1 file †† |
| `risky_paths`           | Repo-fit/Burden    | 0.75   | 3.5%   | Touches API / migrations / deps / CI / auth / security / payment paths | 1 file †† |
| `file_maintenance`      | Repo-fit/Burden    | 0.5    | 2.3%   | Touches vendored / generated / deprecated files (+ optional recency) | 1 file †† |
| `linked_issue`          | Repo-fit/Burden    | 0.5    | 2.3%   | References an issue (`#123`, `fixes #`, `/issues/n`)       | 0         |
| `duplicate_work`        | Repo-fit/Burden    | 0.5    | 2.3%   | Duplicates an existing in-repo PR (exact title)           | 1 search  |
| `signoff`               | Repo-fit/Burden    | 0.75   | 3.5%   | DCO `Signed-off-by:` satisfied (**opt-in**)               | 1 commit §|
| `related_work`          | LLM (optional)     | 0.75   | 3.5%   | Author's prior work relevant to this repo (non_llm or llm) | 1 search ‡|
| `contribution_rules`    | LLM (optional)     | 0.5    | 2.3%   | PR adherence to CONTRIBUTING.md / template (llm only)       | 0–1 file  |
| `pr_template`           | LLM (optional)     | 0.5    | 2.3%   | PR template completion (`${VARIABLE}` fields) (non_llm or llm) | 1 file    |
| `scope_alignment`       | LLM (optional)     | 0.5    | 2.3%   | Aligns with documented scope/roadmap/architecture (non_llm or llm) | 0–2 files |
| `pr_body_quality`       | LLM (optional)     | 0.75   | 3.5%   | Body quality / slop / vague phrases (non_llm or llm)      | 0         |

† `account_age` and `bio_positioning` **share one** cached `/users/:login`
call.
†† `tests_included`, `change_scope`, `risky_paths`, and `file_maintenance`
**share one** cached `GET /pulls/{n}/files` call.
‡ `related_work` adds one cached `GET /repos/{o}/{r}` + one search; with
`provider: llm` it also calls GitHub Models (see below).
§ `signoff` only makes the commits fetch when `signoff.required: true` (opt-in).
The five LLM signals are **off by default** and excluded from the score
unless enabled; `signoff` is skipped unless opted in.

Default per PR: 1 user lookup + 1 shared file-list fetch + ~10 search
requests. With LLM signals on, add GitHub Models calls; with `signoff`
or `file_maintenance.check_recency` on, add commits lookups.

---

## PR-shape signals

### `lines_changed` — weight 1.0

Input = `additions + deletions`. Too-tiny (cosmetic) **and** too-huge
(file-dump) PRs are both suspicious; legit PRs cluster in a middle range.

| Total lines changed | Raw score |
|---------------------|:---------:|
| 0                   | 100       |
| 1 – 10 (`very_tiny_max`)   | 100 → 60 (linear) |
| 11 – 50 (`tiny_max`)      | 60 → 30 (linear)  |
| 51 – 300 (`normal_max`)    | 30 → 10 (linear)  |
| 301 – 800 (`large_max`)    | 10 → 60 (linear)  |
| > 800               | 60 → 100 (`min(100, 60 + (n−800)·0.1)`) |

### `files_changed` — weight 1.0

Input = number of modified files.

| Files changed | Raw score |
|---------------|:---------:|
| 0             | 100       |
| 1 – 3 (`low_max`)   | 40 → 20 (linear) |
| 4 – 10 (`med_max`)  | 20 → 10 (linear) |
| > 10          | 10 → 100 (`min(100, 10 + (n−10)·6)`) |

### `account_age` — weight 1.5

Input = age (days) of the author's GitHub account.

| Account age | Raw score |
|-------------|:---------:|
| (no username / API failure) | 50 |
| ≤ 7 days (`very_new_days`)       | 100 |
| 8 – 30 days (`new_days`)          | 90 → 60 (linear) |
| 31 – 180 days (`medium_days`)    | 60 → 25 (linear) |
| 181 – 365 days (`established_days`) | 25 → 10 (linear) |
| > 365 days  | 10 → 0 (`max(0, 10 − (age−365)·0.05)`) |

### `cross_repo_prs` — weight 2.0

Input = PRs opened by the author across **all** public repos in the last
`lookback_days` (default 7).

| PR count (7-day window) | Raw score |
|-------------------------|:---------:|
| (no username / API failure) | 50 |
| 0 – 2 (`low_max`)   | 0 / 5 / 10 (`count × 5`) |
| 3 – 5 (`med_max`)   | 10 → 35 (linear) |
| 6 – 10 (`high_max`) | 35 → 70 (linear) |
| > 10                | 70 → 100 (`min(100, 70 + (n−10)·4)`) |

---

## Contributor-trust signals

These **lower** the score for trusted contributors (merged history,
engagement, established accounts) and **raise** it for suspicious ones
(first-timers, rejected work, mass-duplicated titles, profile-farming bios).

### `association` — weight 1.5

Input = `author_association` from the PR payload (no API call).

| `author_association` | Raw score |
|----------------------|:---------:|
| `OWNER`, `MEMBER`, `COLLABORATOR` | 0 |
| `CONTRIBUTOR` (has had a PR merged here before) | 15 |
| `FIRST_TIME_CONTRIBUTOR` | 60 |
| `FIRST_TIMER`            | 70 |
| `NONE`, `MANNEQUIN`      | 80 |
| (missing / unknown)      | 80 (`default`) |

### `repo_merge_history` — weight 1.5

Input = previously **merged** PRs by the author in *this* repo.

| Merged PRs in this repo | Raw score |
|-------------------------|:---------:|
| (no username / API failure) | 50 |
| 0 (`none_max`)   | 80 |
| 1 – 2 (`few_max`)  | 80 → 30 (linear) |
| 3 – 5 (`some_max`) | 30 → 10 (linear) |
| > 5             | 10 → 0 (`max(0, 10 − (n−5)·1)`) |

### `closed_unmerged_ratio` — weight 1.0

Input = `closed_unmerged ÷ (closed_unmerged + merged)` across all repos.

| Closed-unmerged ratio | Raw score |
|-----------------------|:---------:|
| (no username / API failure) | 50 |
| no closed PRs at all | 50 (neutral) |
| ≤ 0.2 (`low_max`)   | 5 → 25 (linear) |
| 0.2 – 0.5 (`med_max`) | 25 → 55 (linear) |
| 0.5 – 0.8 (`high_max`) | 55 → 85 (linear) |
| > 0.8               | 85 → 100 (`min(100, 85 + (ratio−0.8)·75)`) |

### `issue_participation` — weight 0.75

Input = issues in *this* repo the author has commented on (proxy for
"discussion before the PR").

| Issue comments in repo | Raw score |
|------------------------|:---------:|
| (no username / API failure) | 50 |
| 0 (`none_max`)   | 70 |
| 1 (`few_max`)    | 70 → 35 (linear) |
| 2 – 5 (`some_max`) | 35 → 12 (linear) |
| > 5             | 12 → 0 (`max(0, 12 − (n−5)·1)`) |

### `review_engagement` — weight 0.75

Input = `engaged ÷ authored` — the share of the author's own PRs where they
also commented (proxy for "responds to review feedback").

| Engagement ratio | Raw score |
|------------------|:---------:|
| (no username / API failure / no PRs) | 50 |
| ≥ 0.8 (`high_min`) | 15 → 5 (linear) |
| 0.4 – 0.8 (`med_min`) | 40 → 15 (linear) |
| 0.1 – 0.4 (`low_min`) | 70 → 40 (linear) |
| < 0.1            | 70 → 80 (`min(100, 70 + (0.1−ratio)·100)`) |

### `duplicate_pr_titles` — weight 1.25

Input = largest cluster of near-identical (normalized) titles ÷ total recent
PRs inspected (`sample_size` = 30).

| Largest duplicate cluster ratio | Raw score |
|---------------------------------|:---------:|
| (no username / API failure) | 50 |
| fewer than 2 PRs | 0 (not enough data) |
| ≤ 0.1 (`low_max`)  | 0 → 10 (linear) |
| 0.1 – 0.3 (`med_max`) | 10 → 45 (linear) |
| 0.3 – 0.6 (`high_max`) | 45 → 80 (linear) |
| > 0.6              | 80 → 100 (`min(100, 80 + (ratio−0.6)·66)`) |

### `bio_positioning` — weight 0.75

Fires only when the author's bio matches a *generic* phrase (e.g.
"open source contributor"). Combined with how little accepted work they have.

| Bio + merged PRs | Raw score |
|------------------|:---------:|
| (no username / API failure) | 50 |
| no bio / no phrases configured | 0 (no signal) |
| bio doesn't match a generic phrase | 0 (not suspicious) |
| generic bio + ≤ 2 merged (`low_merged_max`) | 90 → 60 (linear) |
| generic bio + 3 – 10 merged (`med_merged_max`) | 60 → 20 (linear) |
| generic bio + > 10 merged | 20 → 0 (`max(0, 20 − (n−10)·1)`) |

### `activity_burstiness` — weight 1.0

*Bursty* = ≥ `burst_count` (5) PRs within `burst_span_hours` (24h).
*Broad* = across ≥ `broad_repos` (3) distinct repos.

| Activity pattern | Raw score |
|------------------|:---------:|
| (no username / API failure) | 50 |
| fewer than `min_count` (3) PRs | 10 (too little data) |
| bursty **and** broad | 90 |
| bursty only | 60 |
| broad only | 35 |
| neither | 15 |

---

## Repo-fit & maintainer-burden signals

These signals look at **what the PR touches** (files, paths, scope) and how
well it **fits the project**. The first six are always on (no LLM cost) and
share one cached `GET /pulls/{n}/files` call; `signoff` is **opt-in**.

### `tests_included` — weight 0.75

Covers *"No tests are included"* (increases burden) and its inverse. Uses the
file list and `test_patterns` (globs; `*` matches any chars incl. `/`).

| Situation | Raw score |
|-----------|:---------:|
| (file-list API failure / empty) | 50 |
| tests-only PR (no non-test files) | 5 (improving tests) |
| code + tests | 15 |
| code, no tests, ≤ `small_max_lines` (50) added+deleted | 40 |
| code, no tests, ≤ `med_max_lines` (300) | 40 → 60 (linear) |
| code, no tests, > `med_max_lines` | 60 → 100 (`min(100, 60 + (n−300)·0.05)`) |

### `change_scope` — weight 0.75

Covers *"Multiple unrelated areas changed"* / *"Change is isolated"*. Input =
number of **distinct top-level directories** touched (root-level files count
as one scope).

| Distinct top-level scopes | Raw score |
|---------------------------|:---------:|
| (file-list API failure) | 50 |
| ≤ `low_max` (1)   | 10 |
| ≤ `med_max` (2)   | 10 → 30 (linear) |
| ≤ `high_max` (4)  | 30 → 65 (linear) |
| > `high_max`       | 65 → 100 (`min(100, 65 + (n−4)·8)`) |

### `risky_paths` — weight 0.75

Covers *"Public API changes"*, *"Database migrations"*, *"Dependency
changes"*, and *"Build, CI, deployment, auth, security, payment, or
networking changes"*. Each file is matched (fnmatch, by full path **or** path segment) against
configurable `groups` of patterns; the score rises with the number of
**distinct risky groups** touched.

| Distinct risky groups touched | Raw score |
|-------------------------------|:---------:|
| (file-list API failure / empty) | 50 |
| ≤ `low_max` (0)   | 5 |
| ≤ `med_max` (2)   | 5 → 55 (linear) |
| ≤ `high_max` (3)  | 55 → 75 (linear) |
| > `high_max`       | 75 → 100 (`min(100, 75 + (n−3)·8)`) |

Default groups (each file matched by full path **or** path segment): `public_api`
(`api`, `public`, `__init__.py`, `index.ts`, `mod.rs`, `lib.rs`, `exports.*`,
`schema.*`, `*.proto`, …), `migrations` (`migrations`, `alembic`, `flyway`,
`*.sql`, …), `dependencies` (`package.json`, `package-lock.json`, `yarn.lock`,
`pnpm-lock.yaml`, `requirements*.txt`, `Pipfile*`, `poetry.lock`, `uv.lock`,
`pyproject.toml`, `setup.py`, `Cargo.*`, `go.mod`, `go.sum`, `pom.xml`,
`build.gradle*`, `Gemfile*`, `composer.*`, …), `ci_build_deploy`
(`.github/workflows`, `Dockerfile*`, `docker-compose*`, `Jenkinsfile*`,
`Makefile`, `CMakeLists.txt`, `tsconfig.json`, `*.tf`, `deploy*`, …),
`security_auth` (`auth`, `security`, `crypto`, `permissions`, `*password*`,
`*secret*`, `*token*`, `*jwt*`, `*oauth*`, …), `payment` (`payment*`, `billing`,
`checkout`, `stripe*`, `*invoice*`, …), `networking` (`network*`, `proxy`,
`gateway`, `server`, `middleware`, `ingress`, `dns`, `cdn`, …). See
`pras_bot/signals/risky_paths.py` for the full list.

### `file_maintenance` — weight 0.5

Covers *"Touches deprecated, archived, generated, or vendored files
unnecessarily"* and *"Touches actively maintained files"*. Files matching
`skip_patterns` (vendored / generated / deprecated) raise the score. When
`check_recency: true` (opt-in), files with no commit in `stale_days` (365)
also raise it (up to `max_files` files checked via per-file commit lookups).

| Skip-file ratio | Raw score (recency off) |
|-----------------|:---------:|
| (file-list API failure / empty) | 50 |
| 0 (no vendored/generated files) | 5 |
| ≤ `low_max` (0.2) | 5 → 25 (linear) |
| ≤ `med_max` (0.5) | 25 → 55 (linear) |
| > `med_max` | 55 → 100 (`min(100, 55 + (ratio−0.5)·80)`) |

With `check_recency: true`, `min(100, score + 40 × stale_ratio)` is added.

Default `skip_patterns` (matched by full path **or** segment): `vendor`,
`third_party`, `third-party`, `node_modules`, `bower_components`, `dist`,
`build`, `out`, `target`, `*.generated.*`, `*.gen.*`, `*_generated.*`,
`*.pb.go`, `*.pb.cc`, `*.pb.h`, `*.g.dart`, `*.min.js`, `*.min.css`, `*.map`,
`zz_*.*`, `*.pb.swift`, `Pods`.

### `linked_issue` — weight 0.5

Covers *"No concrete bug, user impact, benchmark, or issue"* and *"Existing
issue explains context"*. Scans the PR title + body for issue references
(`#123`, `fixes #`, `closes #`, `resolves #`, `refs #`, `/issues/123`). No
API call.

| Situation | Raw score |
|-----------|:---------:|
| references an issue | 10 |
| no reference, ≤ `small_max_lines` (50) added+deleted | 30 |
| no reference, > `small_max_lines` | 55 |

### `duplicate_work` — weight 0.5

Covers *"Duplicates existing work"* / *"Reopens already rejected ideas"*.
Compares the PR's normalized title to recent PRs in **this** repo (1 search).

| Match against an existing in-repo PR | Raw score |
|---------------------------------------|:---------:|
| (search API failure) | 50 |
| no similar PR | 5 |
| exact title match, existing PR is **OPEN** | 85 |
| exact title match, existing PR is **CLOSED** | 60 |

### `signoff` — weight 0.75 — opt-in

Covers *"Satisfies CLA, DCO, sign-off"*. Checks every commit for a
`Signed-off-by:` trailer. **CLA** itself needs a dedicated bot (e.g.
cla-assistant) and is out of scope. Off by default (`required: false` →
skipped); turn on only for repos that require DCO.

| `signoff.required` | Situation | Raw score |
|--------------------|-----------|:---------:|
| `false` (default) | — | skipped (`None`) |
| `true` | (commits API failure) | 50 |
| `true` | all commits signed-off | 0 |
| `true` | some commits missing sign-off | 70 |
| `true` | all commits missing sign-off | 85 |

---

## LLM-powered signals (optional)

These five signals cover checklist items that need natural-language judgment
(topic relevance, rule adherence, template completion, scope/roadmap fit, body
quality / slop detection). They are **opt-in** and **off by default**,
and each lets you choose how it runs via a `provider` setting so you stay in
control of cost:

| `provider`           | Meaning |
|----------------------|------------------------------------------------------|
| `off` (default)      | Signal disabled — excluded from the score entirely |
| `non_llm`            | Pure-Python heuristic (no dependencies, no API cost) |
| `llm`                | GitHub Models (an LLM). **Costs money / quota.** |

### Enabling the LLM path

1. Add `permissions: models: read` to your workflow (see [README](README.md)).
2. Set `llm.enabled: true` in `.github/pras-bot.yml`.
3. Set `provider: llm` on the signals you want.

```yaml
llm:
  enabled: true
  model: "openai/gpt-4o-mini"   # github.com/marketplace/models
  temperature: 0.0               # 0 = deterministic
  max_tokens: 256
  timeout: 30

signals:
  related_work:
    provider: llm          # or non_llm (no cost) / off
  contribution_rules:
    provider: llm          # non_llm NOT supported here
  pr_template:
    provider: non_llm     # or llm / off (default)
    template_path: ".github/PULL_REQUEST_TEMPLATE.md"   # repo-relative
  scope_alignment:
    provider: llm         # or non_llm / off (default)
    reference_docs: ["ROADMAP.md", "ARCHITECTURE.md"]   # repo-relative
  pr_body_quality:
    provider: non_llm     # or llm / off (default)
```

If `llm.enabled` is `false`, any `provider: llm` signal is **silently
skipped** (dropped from the average) — it never biases the score toward a
neutral 50. The same happens on any API/parse error (degrades to neutral 50).

### Response handling

LLM signals ask the model to return **only** a JSON object like
`{"score": <0-100>}`. Responses are parsed by a brace-matching extractor
(`extract_first_json`) that tolerates markdown fences and surrounding prose,
and the score is clamped to `[0, 100]`. Any parse failure or API error
degrades the signal to a neutral **50** (never crashes the run).

### `related_work` — weight 0.75

Covers *"Has relevant merged PRs in similar projects"* and *"Contribution
history consistent with the repo's ecosystem."*

| `provider` | How it scores |
|------------|------------------------------------------------------|
| `non_llm`  | Token-overlap (Jaccard) between the repo description/topics/language and the author's recent PR titles. |
| `llm`      | GitHub Models judges topical relatedness from the same inputs. |

| Token overlap (`non_llm`) | Raw score |
|---------------------------|:---------:|
| (no username / API failure) | 50 |
| ≤ `low_overlap` (0.05)   | 70 (unrelated) |
| ≥ `high_overlap` (0.25)  | 15 (clearly related / trusted) |
| between                  | 70 → 15 (linear) |

The `llm` path returns the model's `score` directly (clamped to [0, 100];
0 = clearly relevant, 100 = completely unrelated).

### `contribution_rules` — weight 0.5

Covers *"Repeatedly ignores project contribution rules."*

| `provider` | How it scores |
|------------|------------------------------------------------------|
| `off`      | Disabled (default). |
| `llm`      | Fetches `CONTRIBUTING.md` (or the PR template) and asks the model how well the PR follows it. |

`non_llm` is **not** supported (interpreting free-form rules needs an LLM);
setting it is treated as `off`.

| Situation | Raw score |
|-----------|:---------:|
| no `CONTRIBUTING.md` / template in repo | skipped (`None`) |
| (LLM / API failure) | 50 |
| otherwise | model's `score` (0 = compliant, 100 = ignores rules), clamped |

### `pr_template` — weight 0.5

Covers *"Completes the PR template properly"* / *"Ignores PR template"*. Reads
the PR template (configurable `template_path`, repo-relative) and checks whether
its fill-in fields were completed.

| `provider` | How it scores |
|------------|------------------------------------------------------|
| `non_llm`  | Extracts `${VARIABLE}` placeholders (and `##` sections) from the template and checks the PR body for unfilled ones. |
| `llm`      | GitHub Models judges how completely the template was filled. |

| Situation | Raw score (`non_llm`) |
|-----------|:---------:|
| no template in repo | skipped (`None`) |
| empty body | 80 |
| `${VAR}` placeholders, unfilled ratio r | 10 → 80 (linear in r) |
| all `${VAR}` filled but body lacks the template's headers | 40 |
| template has only `##` sections, missing ratio m | 10 → 60 (linear in m) |
| template has no placeholders / sections | skipped (`None`) |
| (LLM / API failure) | 50 |

Write templates with `${SUMMARY}`, `${WHY}`, `${TESTING}` style placeholders;
an unfilled `${...}` left verbatim in the PR body scores high.

### `scope_alignment` — weight 0.5

Covers *"Respects project scope and roadmap"*, *"Matches existing
architecture"*, *"Conflicts with existing roadmap or issue discussion"*, and
*"Changes architecture without maintainer request"*. Reads one or more
reference docs (`reference_docs`, repo-relative; defaults to `ROADMAP.md` and
`ARCHITECTURE.md`, missing ones are skipped).

| `provider` | How it scores |
|------------|------------------------------------------------------|
| `non_llm`  | Token-overlap (Jaccard) between the PR title+body and the reference docs. |
| `llm`      | GitHub Models judges alignment from the same inputs. |

| Token overlap (`non_llm`) | Raw score |
|---------------------------|:---------:|
| no reference docs found | skipped (`None`) |
| (API failure) | 50 |
| ≤ `low_overlap` (0.05) | 65 (misaligned) |
| ≥ `high_overlap` (0.2) | 15 (aligned) |
| between | 65 → 15 (linear) |

The `llm` path returns the model's `score` (0 = aligned, 100 = misaligned).

### `pr_body_quality` — weight 0.75

Covers *"Generic PR body with polished but shallow language"*, *"Overuse of
vague phrases"* (`improves maintainability`, `enhances performance`, `follows
best practices`), and the low-risk inverse (clear, substantial body). No
reference doc; `vague_phrases` is a configurable list.

| `provider` | How it scores |
|------------|------------------------------------------------------|
| `non_llm`  | Body length bucket + count of `vague_phrases` present. |
| `llm`      | GitHub Models judges slop / vagueness / concreteness. |

| Body (`non_llm`) | Raw score |
|------------------|:---------:|
| empty / None | 80 |
| < `short_min_chars` (50) | 55 |
| < `med_min_chars` (200) | 35 |
| ≥ `med_min_chars` | 15 |
| + vague phrases | `min(100, base + min(45, hits × 12))` |

The `llm` path returns the model's `score` (0 = concrete & useful, 100 = slop).

---

## Label mapping

The final score is checked top-down; the **first** matching threshold wins.
Only one pras-bot label is applied at a time, and stale labels from previous
runs are removed automatically.

| Final score | Label | Color |
|:-----------:|-------|:-----:|
| ≥ 70 | `likely-spam` 🚩 | `ff0000` |
| ≥ 40 | `needs-review` 🟡 | `ffaa00` |
| ≥ 0  | `looks-good` ✅ | `0e8a16` |

---

## Checklist items not implemented as separate signals

These remain subjective or need the **per-PR diff** (which the bot avoids for
cost/resilience), so they are intentionally not separate signals:

- *PR title/body sounds useful but diff is trivial or unrelated* — needs the diff.
- *Adds comments / wrappers / abstractions, or refactors without need* / *makes code more verbose without improving behavior* — needs diff.
- *Fixes “potential” issues without proof* / *security/performance claims without exploit or benchmark* — needs diff + benchmarks.
- *Prior PRs show tests, context, follow-through* — needs diff/file analysis of past PRs (partly covered by `related_work` and the PR-shape signals).
- *Maintainers have previously interacted positively* / *responds constructively to review feedback* — sentiment of review comments (needs comment text). `review_engagement` covers the engagement-ratio proxy.

> Now covered by the new repo-fit / burden / LLM signals: *"completes the PR
> template"* → `pr_template`; *"respects scope/roadmap"* / *"matches
> architecture"* → `scope_alignment`; *"generic / vague PR body"* →
> `pr_body_quality`; *"no tests"* → `tests_included`; *"multiple unrelated
> areas"* → `change_scope`; *"public API / migrations / deps / CI / auth /
> payment changes"* → `risky_paths`; *"touches vendored / generated /
> deprecated files"* → `file_maintenance`; *"no concrete issue"* →
> `linked_issue`; *"duplicates existing work"* → `duplicate_work`; *"DCO /
> sign-off"* → `signoff`. *Profile-farming / cosmetic-PR patterns* were
> already captured by `cross_repo_prs` + `duplicate_pr_titles` +
> `closed_unmerged_ratio` + the PR-shape signals.
