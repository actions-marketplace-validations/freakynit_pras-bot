# PRAS Bot — PR Anti-Spam Bot

**GitHub Action that scores every incoming PR for spam signals and auto-labels it.**

Think of it as a first-line triage filter. It doesn't close PRs automatically — it
labels them so maintainers can decide what deserves attention.

## Signals

Every incoming PR is scored **0–100** (0 = legit, 100 = spam) from a
weighted average of **25 signals** — 4 *PR-shape*, 8 *contributor-trust*,
7 *repo-fit & maintainer-burden*, and 6 optional *LLM-powered* signals
(off by default) — then mapped to a single label (`likely-spam` /
`needs-review` / `looks-good`). Each signal returns a raw score in
[0, 100] (or `None` to skip itself); the final score is
`Σ(raw × weight) ÷ Σ(weights)` over the enabled signals.

For the full list of signals, their weights, scoring curves, and which
checklist items each one covers, see 👉 **[`SIGNALS.md`](SIGNALS.md)**.

### Optional: LLM-powered signals (cost money)

Six signals — `related_work`, `contribution_rules`, `diff_credibility`,
`pr_template`, `scope_alignment`, and `pr_body_quality` — can use GitHub Models (an LLM)
for natural-language judgment (topic relevance, rule adherence, template
completion, scope/roadmap alignment, body-quality / slop detection).
They're **off by default**. Each has a `provider` (`off` / `non_llm` / `llm`)
so you choose cost vs. accuracy per signal — `non_llm` is a free
pure-Python heuristic, `llm` calls GitHub Models. (`contribution_rules`
supports `off`/`llm` only.) To enable the LLM path:

1. Uncomment `models: read` in your workflow `permissions:` (above).
2. In `.github/pras-bot.yml` set `llm.enabled: true` and `provider: llm` on
   the signals you want.

### Reference docs for NLP signals (configurable paths)

Some signals read a **reference document** from your repo whose path you can
configure (paths are **relative to the repo root**):

| Signal             | Reference doc (default)                         | What it's used for |
|--------------------|--------------------------------------------------|--------------------|
| `contribution_rules`| `CONTRIBUTING.md` (fallback: PR template)       | rule adherence |
| `pr_template`      | `.github/PULL_REQUEST_TEMPLATE.md`              | template completion |
| `scope_alignment`  | `ROADMAP.md`, `ARCHITECTURE.md` (both tried)    | scope/roadmap/architecture fit |

For `pr_template`, write the template with `${VARIABLE}` placeholders for
the fill-in fields you want checked, e.g.:

```markdown
## Summary
${SUMMARY}

## Why
${WHY}

## Testing
${TESTING}
```

The bot checks whether the PR body still contains an unfilled `${...}`
(e.g. the contributor left `${SUMMARY}` verbatim) and scores accordingly.
`scope_alignment` just reads the doc text (no placeholders). Missing docs
are skipped — a signal never crashes because a file is absent.

LLM responses are parsed defensively (markdown fences tolerated, scores
clamped to [0, 100], failures degrade to a neutral 50 — never crash). Full
details in [`SIGNALS.md`](SIGNALS.md).

For diff-aware LLM checks, the bot uses GitHub's existing PR files response
(`patch` fields) and sends only a bounded subset: by default all patchable
files for PRs with up to three files, or the top three files by additions for
larger PRs, capped at 5000 characters of patch context.

## Installation

### 1. Add the workflow

Create `.github/workflows/pras-bot.yml` in your repo:

```yaml
name: PR Anti-Spam Bot
on:
  pull_request_target:
    types: [opened, synchronize]

permissions:
  pull-requests: write
  issues: write        # for labelling
  contents: read
  # models: read      # ONLY if you enable the optional LLM signals (see SIGNALS.md)

jobs:
  pras-bot:
    runs-on: ubuntu-latest
    steps:
      - uses: your-org/pras-bot@v1   # after publishing to marketplace
        # OR during development:
        # uses: ./pras-bot
        with:
          config_path: ".github/pras-bot.yml"    # optional
```

> **Important:** Use `pull_request_target` NOT `pull_request` so the
> action has write access to the target repo and access to the `GITHUB_TOKEN`.

### 2. (Optional) Customize configuration

Add `.github/pras-bot.yml` to override defaults:

```yaml
weights:
  lines_changed:         1.0
  files_changed:         1.0
  account_age:           1.5
  cross_repo_prs:        2.0
  association:           1.5
  repo_merge_history:    1.5
  closed_unmerged_ratio: 1.0
  issue_participation:   0.75
  review_engagement:     0.75
  duplicate_pr_titles:   1.25
  bio_positioning:       0.75
  activity_burstiness:   1.0
  # repo-fit & maintainer-burden (see SIGNALS.md)
  tests_included:        0.75
  change_scope:          0.75
  risky_paths:           0.75
  file_maintenance:      0.5
  linked_issue:          0.5
  duplicate_work:        0.5
  signoff:               0.75   # opt-in: only counts when signoff.required=true
  # optional LLM-powered (off by default; excluded unless enabled)
  related_work:          0.75
  contribution_rules:    0.5
  diff_credibility:      0.75
  pr_template:           0.5
  scope_alignment:       0.5
  pr_body_quality:       0.75

labels:
  - threshold: 70
    name: "likely-spam"
    color: "ff0000"
    description: "High probability of being AI-generated spam"
  - threshold: 40
    name: "needs-review"
    color: "ffaa00"
    description: "Some suspicious signals — take a closer look"
  - threshold: 0
    name: "looks-good"
    color: "0e8a16"
    description: "Passed automated spam checks"

comment: true   # post a scorecard comment on the PR

signals:
  account_age:
    thresholds:
      very_new_days: 7
      new_days: 30
      medium_days: 180
      established_days: 365
  cross_repo_prs:
    lookback_days: 7
    thresholds:
      low_max: 2
      med_max: 5
      high_max: 10
  association:
    scores:
      OWNER: 0
      MEMBER: 0
      COLLABORATOR: 0
      CONTRIBUTOR: 15
      FIRST_TIME_CONTRIBUTOR: 60
      NONE: 80
    default: 80
  repo_merge_history:
    thresholds:
      none_max: 0
      few_max: 2
      some_max: 5
  # …see config/default_config.yml for closed_unmerged_ratio,
  # issue_participation, review_engagement, duplicate_pr_titles,
  # bio_positioning, activity_burstiness, and the repo-fit / maintainer-burden
  # signals (tests_included, change_scope, risky_paths, file_maintenance,
  # linked_issue, duplicate_work, signoff) and the optional LLM signals
  # (diff_credibility, pr_template, scope_alignment, pr_body_quality) tuning knobs.

  # Example: opt into DCO sign-off enforcement + PR-template completion
  signoff:
    required: true
  pr_template:
    provider: non_llm   # or llm (needs llm.enabled) / off (default)
    template_path: ".github/PULL_REQUEST_TEMPLATE.md"
```

If you **don't** add a config, the [built-in defaults](config/default_config.yml) are used.

## How scoring works

1. Every signal computes a **raw score** ∈ [0, 100]
2. Raw score × **weight** (from config) = weighted contribution
3. Final score = Σ(weighted) ÷ Σ(weights) — always stays in [0, 100]
4. Labels are checked top-down (highest threshold first):
   - If score ≥ 70 → `likely-spam` 🚩
   - If score ≥ 40 → `needs-review` 🟡
   - Otherwise → `looks-good` ✅

Only **one** label is applied at a time. Stale bot labels from previous runs
are cleaned up automatically.

## Architecture

```
pras-bot/
├── action.yml               # GitHub Action composite definition
├── pyproject.toml
├── config/
│   └── default_config.yml   # built-in defaults
├── pras_bot/
│   ├── main.py              # entry point
│   ├── config_loader.py     # merge user config over defaults
│   ├── github_client.py     # REST + GraphQL API wrapper
│   ├── scorer.py            # weighted scoring + label selection
│   ├── json_util.py                # extract JSON from LLM responses
│   └── signals/
│       ├── base.py                  # ScoredSignal base + linear()/clamp_score() helpers
│       ├── lines_changed.py
│       ├── files_changed.py
│       ├── account_age.py
│       ├── cross_repo_prs.py
│       ├── association.py
│       ├── repo_merge_history.py
│       ├── closed_unmerged_ratio.py
│       ├── issue_participation.py
│       ├── review_engagement.py
│       ├── duplicate_pr_titles.py
│       ├── bio_positioning.py
│       ├── activity_burstiness.py
│       ├── related_work.py         # LLM/non-LLM: relevance to this repo
│       ├── contribution_rules.py   # LLM-only: adherence to CONTRIBUTING.md
│       ├── diff_credibility.py     # LLM-only: PR claims match the diff
│       ├── pr_template.py          # LLM/non-LLM: PR template completion (${VAR})
│       ├── scope_alignment.py      # LLM/non-LLM: roadmap/architecture fit
│       ├── pr_body_quality.py      # LLM/non-LLM: body quality / slop
│       ├── tests_included.py       # does the PR add tests?
│       ├── change_scope.py         # sprawl across top-level areas
│       ├── risky_paths.py          # touches API/migrations/deps/CI/auth…
│       ├── file_maintenance.py     # touches vendored/generated/deprecated
│       ├── linked_issue.py         # references an issue
│       ├── duplicate_work.py       # duplicates an existing in-repo PR
│       └── signoff.py              # DCO Signed-off-by (opt-in)
└── README.md
```

### Adding a new signal

1. Create `pras_bot/signals/my_signal.py`:
   ```python
   from .base import ScoredSignal

   class MySignal(ScoredSignal):
       def score(self) -> float:
           # access self.gh, self.config, self.pr_data
           return 0.0  # 0-100
   ```
2. Register it in `pras_bot/main.py` in `_SIGNAL_REGISTRY`.
3. Add a `signals.my_signal` section and a `weights.my_signal` key to the config.

The class name `MySignal` automatically maps to config key `my_signal`.

## Local testing

```bash
pip install -e .
GITHUB_REPOSITORY="owner/repo" \
GITHUB_TOKEN="ghp_..." \
GITHUB_EVENT_PATH="test_fixtures/pr_opened.json" \
python -m pras_bot.main
```

Create a `test_fixtures/pr_opened.json` file with a sample GitHub webhook payload.

## Limits & notes

- The cross-repo + trust signals use GitHub's REST search API, which is
  rate-limited (10 req/min unauthenticated, 30 req/min with token). A single
  PR run issues ~10 search requests plus 1 user lookup, a paginated file-list
  fetch (`pulls/{n}/files`, shared by the repo-fit/burden and diff-aware
  LLM signals) and 1 in-repo
  duplicate search — fine for normal volume, but it can rate-limit on very
  high-traffic repos.
- Every signal that hits the API degrades to a neutral score on failure
  (rate limit, 5xx, network) instead of crashing the whole run.
- `association`, the PR-shape signals, and `linked_issue` need no extra API
  calls. `signoff` (`required: true`) and `file_maintenance`
  (`check_recency: true`) are **opt-in** and add 1 commits fetch / up to
  `max_files` per-file commit lookups respectively.
- The action runs on `pull_request_target` so it has access to repo-level secrets.
