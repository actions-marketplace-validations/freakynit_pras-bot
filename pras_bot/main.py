"""PR Anti-Spam Bot (pras-bot) — main entry point for the GitHub Action."""

from __future__ import annotations

import json
import os
import sys

from .config_loader import load_config
from .scorer import compute_spam_score, compute_labels_from_score
from .github_client import GitHubClient
from .signals.base import ScoredSignal
from .signals.lines_changed import LinesChangedSignal
from .signals.files_changed import FilesChangedSignal
from .signals.account_age import AccountAgeSignal
from .signals.cross_repo_prs import CrossRepoPRsSignal
from .signals.association import AssociationSignal
from .signals.repo_merge_history import RepoMergeHistorySignal
from .signals.closed_unmerged_ratio import ClosedUnmergedRatioSignal
from .signals.issue_participation import IssueParticipationSignal
from .signals.review_engagement import ReviewEngagementSignal
from .signals.duplicate_pr_titles import DuplicatePRTitlesSignal
from .signals.bio_positioning import BioPositioningSignal
from .signals.activity_burstiness import ActivityBurstinessSignal
from .signals.related_work import RelatedWorkSignal
from .signals.contribution_rules import ContributionRulesSignal
from .signals.diff_credibility import DiffCredibilitySignal
from .signals.pr_template import PrTemplateSignal
from .signals.scope_alignment import ScopeAlignmentSignal
from .signals.pr_body_quality import PrBodyQualitySignal
from .signals.tests_included import TestsIncludedSignal
from .signals.change_scope import ChangeScopeSignal
from .signals.risky_paths import RiskyPathsSignal
from .signals.file_maintenance import FileMaintenanceSignal
from .signals.linked_issue import LinkedIssueSignal
from .signals.duplicate_work import DuplicateWorkSignal
from .signals.signoff import SignoffSignal

_SIGNAL_REGISTRY: list[type[ScoredSignal]] = [
    # PR-shape
    LinesChangedSignal,
    FilesChangedSignal,
    # Contributor-trust
    AccountAgeSignal,
    CrossRepoPRsSignal,
    AssociationSignal,
    RepoMergeHistorySignal,
    ClosedUnmergedRatioSignal,
    IssueParticipationSignal,
    ReviewEngagementSignal,
    DuplicatePRTitlesSignal,
    BioPositioningSignal,
    ActivityBurstinessSignal,
    # Repo-fit & maintainer-burden (file-list / body based)
    TestsIncludedSignal,
    ChangeScopeSignal,
    RiskyPathsSignal,
    FileMaintenanceSignal,
    LinkedIssueSignal,
    DuplicateWorkSignal,
    SignoffSignal,
    # Optional LLM-powered (off by default)
    RelatedWorkSignal,
    ContributionRulesSignal,
    DiffCredibilitySignal,
    PrTemplateSignal,
    ScopeAlignmentSignal,
    PrBodyQualitySignal,
]


def _env(key: str) -> str:
    """Read a required environment variable; exit with message if missing."""
    val = os.getenv(key, "").strip()
    if not val:
        print(f"❌  required env var {key} is missing or empty")
        sys.exit(1)
    return val


def run() -> None:
    """Run the full scoring pipeline."""

    # ---- gather inputs ---------------------------------------------------
    token = _env("GITHUB_TOKEN")
    repo_full = _env("GITHUB_REPOSITORY")          # "owner/repo"
    event_path = _env("GITHUB_EVENT_PATH")

    with open(event_path, encoding="utf-8") as f:
        event = json.load(f)

    pr_number: int | None = event.get("pull_request", {}).get("number") or event.get("number")
    if pr_number is None:
        print("⚠️  no pull_request found in event payload — nothing to score")
        sys.exit(0)

    pr_node_id: str | None = None
    if isinstance(event.get("pull_request"), dict):
        pr_node_id = event["pull_request"].get("node_id")

    # ---- load configuration ------------------------------------------------
    config = load_config(os.getenv("INPUT_CONFIG_PATH", "").strip() or None, token, repo_full)
    repo_owner, repo_name = repo_full.split("/", 1)

    # ---- instantiate GitHub client -----------------------------------------
    gh = GitHubClient(token, repo_owner, repo_name, pr_number, pr_node_id, config.get("llm", {}))

    # ---- fetch PR details --------------------------------------------------
    pr_data = gh.fetch_pr_details()

    # ---- build signal instances & score ------------------------------------
    signals: list[ScoredSignal] = [
        cls(gh, config, pr_data) for cls in _SIGNAL_REGISTRY  # type: ignore[abstract]
    ]

    final_score, breakdown = compute_spam_score(signals, config)

    print(f"📊  spam score: {final_score:.1f} / 100")
    for s in breakdown:
        if s["raw"] is None:
            print(f"    • {s['name']:25s} — skipped (provider off / llm disabled)")
        else:
            print(f"    • {s['name']:25s} — weighted: {s['weighted']:5.1f}  (raw: {s['raw']:.1f}, weight: {s['weight']:.2f})")

    # ---- determine labels --------------------------------------------------
    chosen_label = compute_labels_from_score(final_score, config)
    gh.apply_labels(chosen_label, config.get("labels", []))

    # ---- optionally comment ------------------------------------------------
    if config.get("comment", True):
        gh.add_comment(final_score, breakdown, chosen_label)

    print("✅  pras-bot finished successfully")


if __name__ == "__main__":
    run()
