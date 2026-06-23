"""Tests for the scoring engine and individual signals."""

import copy
import io
import json
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

# Allow importing the package during development
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import yaml

from pras_bot.github_client import GitHubAPIError, GitHubClient
from pras_bot.config_loader import _deep_merge
from pras_bot.scorer import compute_spam_score, compute_labels_from_score
from pras_bot.signals.lines_changed import LinesChangedSignal
from pras_bot.signals.files_changed import FilesChangedSignal
from pras_bot.signals.account_age import AccountAgeSignal
from pras_bot.signals.cross_repo_prs import CrossRepoPRsSignal
from pras_bot.signals.association import AssociationSignal
from pras_bot.signals.repo_merge_history import RepoMergeHistorySignal
from pras_bot.signals.closed_unmerged_ratio import ClosedUnmergedRatioSignal
from pras_bot.signals.issue_participation import IssueParticipationSignal
from pras_bot.signals.review_engagement import ReviewEngagementSignal
from pras_bot.signals.duplicate_pr_titles import DuplicatePRTitlesSignal
from pras_bot.signals.bio_positioning import BioPositioningSignal
from pras_bot.signals.activity_burstiness import ActivityBurstinessSignal
from pras_bot.signals.related_work import RelatedWorkSignal
from pras_bot.signals.contribution_rules import ContributionRulesSignal
from pras_bot.signals.diff_credibility import DiffCredibilitySignal
from pras_bot.signals.pr_template import PrTemplateSignal
from pras_bot.signals.scope_alignment import ScopeAlignmentSignal
from pras_bot.signals.pr_body_quality import PrBodyQualitySignal
from pras_bot.signals.tests_included import TestsIncludedSignal
from pras_bot.signals.change_scope import ChangeScopeSignal
from pras_bot.signals.risky_paths import RiskyPathsSignal
from pras_bot.signals.file_maintenance import FileMaintenanceSignal
from pras_bot.signals.linked_issue import LinkedIssueSignal
from pras_bot.signals.duplicate_work import DuplicateWorkSignal
from pras_bot.signals.signoff import SignoffSignal
from pras_bot.json_util import extract_first_json


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = yaml.safe_load(
    (Path(__file__).resolve().parent.parent / "config" / "default_config.yml").read_text()
)


def _signal(name: str, score: float) -> MagicMock:
    m = MagicMock()
    m.name.return_value = name
    m.score.return_value = score
    return m


# ---------------------------------------------------------------------------
# scorer tests
# ---------------------------------------------------------------------------

class TestScorer:
    def test_weighted_average(self):
        config = {
            "weights": {"a": 1, "b": 2},
            "labels": [],
        }
        s1 = _signal("a", 20)
        s2 = _signal("b", 80)
        final, breakdown = compute_spam_score([s1, s2], config)
        # (20*1 + 80*2) / (1+2) = 180/3 = 60
        assert final == 60.0
        assert len(breakdown) == 2

    def test_clamped_at_100(self):
        config = {"weights": {"sig": 1}, "labels": []}
        s = _signal("sig", 150)
        final, _ = compute_spam_score([s], config)
        assert final == 100.0

    def test_clamped_at_0(self):
        config = {"weights": {"sig": 1}, "labels": []}
        s = _signal("sig", -10)
        final, _ = compute_spam_score([s], config)
        assert final == 0.0

    def test_zero_total_weight(self):
        final, _ = compute_spam_score([], {"weights": {}, "labels": []})
        assert final == 0.0


# ---------------------------------------------------------------------------
# label tests
# ---------------------------------------------------------------------------

class TestLabels:
    def test_picks_most_severe_matching_label(self):
        config = {
            "weights": {},
            "labels": [
                {"threshold": 80, "name": "high"},
                {"threshold": 50, "name": "med"},
                {"threshold": 0, "name": "low"},
            ],
        }
        # score 90 → highest matching threshold is "high"
        assert compute_labels_from_score(90, config)["name"] == "high"
        # score 60 → "med"
        assert compute_labels_from_score(60, config)["name"] == "med"
        # score 30 → only the 0-threshold "low" matches
        assert compute_labels_from_score(30, config)["name"] == "low"
        # score = 0 (min clamped) → "low"
        assert compute_labels_from_score(0, config)["name"] == "low"

    def test_no_matching_label_returns_none(self):
        config = {"weights": {}, "labels": [{"threshold": 50, "name": "high"}]}
        assert compute_labels_from_score(10, config) is None

    def test_empty_labels_config_returns_none(self):
        assert compute_labels_from_score(50, {"weights": {}, "labels": []}) is None

    def test_default_config_labels(self):
        assert compute_labels_from_score(90, DEFAULT_CONFIG)["name"] == "likely-spam"
        assert compute_labels_from_score(50, DEFAULT_CONFIG)["name"] == "needs-review"
        assert compute_labels_from_score(10, DEFAULT_CONFIG)["name"] == "looks-good"


class TestConfigMerge:
    def test_deep_merge_does_not_mutate_inputs(self):
        base = {"a": {"b": 1}, "c": [1]}
        override = {"a": {"d": 2}, "c": [2]}
        merged = _deep_merge(base, override)

        assert merged == {"a": {"b": 1, "d": 2}, "c": [2]}
        assert base == {"a": {"b": 1}, "c": [1]}
        assert override == {"a": {"d": 2}, "c": [2]}


# ---------------------------------------------------------------------------
# signal unit tests (pure logic, no API calls)
# ---------------------------------------------------------------------------

class MockGH:
    """Minimal mock that signal classes can call."""
    def __init__(self, account_age_days: int = 365, cross_repo_count: int = 0):
        import datetime
        self.created_at = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=account_age_days)
        ).isoformat()
        self.cross_repo_count = cross_repo_count

    def fetch_user_created_at(self, username: str) -> str:
        return self.created_at

    def search_prs_by_author(self, username: str, lookback_days: int = 7) -> int:
        return self.cross_repo_count


class TestLinesChanged:
    def test_empty_pr(self):
        sig = LinesChangedSignal(None, DEFAULT_CONFIG, {"additions": 0, "deletions": 0})
        assert sig.score() == 100.0

    def test_normal_pr(self):
        sig = LinesChangedSignal(None, DEFAULT_CONFIG, {"additions": 80, "deletions": 20})
        s = sig.score()
        # should be in the "normal" sweet spot → low score (< 30)
        assert 0 <= s <= 30, f"got {s}"

    def test_very_tiny(self):
        sig = LinesChangedSignal(None, DEFAULT_CONFIG, {"additions": 3, "deletions": 0})
        s = sig.score()
        assert 60 <= s <= 100

    def test_huge(self):
        sig = LinesChangedSignal(None, DEFAULT_CONFIG, {"additions": 1500, "deletions": 500})
        s = sig.score()
        assert s > 60


class TestFilesChanged:
    def test_single_file(self):
        sig = FilesChangedSignal(None, DEFAULT_CONFIG, {"changed_files": 1})
        s = sig.score()
        assert 20 <= s <= 40

    def test_many_files(self):
        sig = FilesChangedSignal(None, DEFAULT_CONFIG, {"changed_files": 20})
        s = sig.score()
        assert s > 50


class TestAccountAge:
    def test_brand_new_account(self):
        gh = MockGH(account_age_days=3)
        pr = {"user": {"login": "spammer123"}}
        sig = AccountAgeSignal(gh, DEFAULT_CONFIG, pr)
        assert sig.score() == 100.0

    def test_established_account(self):
        gh = MockGH(account_age_days=500)
        pr = {"user": {"login": "legit-dev"}}
        sig = AccountAgeSignal(gh, DEFAULT_CONFIG, pr)
        assert sig.score() < 15

    def test_unknown_user(self):
        sig = AccountAgeSignal(None, DEFAULT_CONFIG, {})
        assert sig.score() == 50.0


class TestCrossRepoPRs:
    def test_zero_prs_is_neutral_no_history(self):
        gh = MockGH(cross_repo_count=0)
        pr = {"user": {"login": "new-dev"}}
        sig = CrossRepoPRsSignal(gh, DEFAULT_CONFIG, pr)
        assert sig.score() == 50.0

    def test_few_prs(self):
        gh = MockGH(cross_repo_count=1)
        pr = {"user": {"login": "normal-dev"}}
        sig = CrossRepoPRsSignal(gh, DEFAULT_CONFIG, pr)
        assert sig.score() < 15

    def test_spammer_volume(self):
        gh = MockGH(cross_repo_count=30)
        pr = {"user": {"login": "spammer"}}
        sig = CrossRepoPRsSignal(gh, DEFAULT_CONFIG, pr)
        assert sig.score() > 80

    def test_unknown_user(self):
        sig = CrossRepoPRsSignal(None, DEFAULT_CONFIG, {})
        assert sig.score() == 50.0


# ---------------------------------------------------------------------------
# signal name / config-key mapping
# ---------------------------------------------------------------------------

class TestSignalNames:
    def test_names_match_config_weight_keys(self):
        classes = [
            LinesChangedSignal,
            FilesChangedSignal,
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
            RelatedWorkSignal,
            ContributionRulesSignal,
            DiffCredibilitySignal,
            PrTemplateSignal,
            ScopeAlignmentSignal,
            PrBodyQualitySignal,
            TestsIncludedSignal,
            ChangeScopeSignal,
            RiskyPathsSignal,
            FileMaintenanceSignal,
            LinkedIssueSignal,
            DuplicateWorkSignal,
            SignoffSignal,
        ]
        signal_names = {cls.name() for cls in classes}
        assert signal_names == set(DEFAULT_CONFIG["weights"].keys())

    def test_cross_repo_name_not_mangled(self):
        # Regression: camelCase splitting mangled "PRs" into "p_rs",
        # which silently dropped the signal's weight + config.
        assert CrossRepoPRsSignal.name() == "cross_repo_prs"

    def test_duplicate_pr_titles_name_not_mangled(self):
        # Same acronym-mangling risk for the "PR" in DuplicatePRTitlesSignal.
        assert DuplicatePRTitlesSignal.name() == "duplicate_pr_titles"


# ---------------------------------------------------------------------------
# resilience — a single signal failing must not crash the whole run
# ---------------------------------------------------------------------------

class TestSignalResilience:
    def test_account_age_neutral_on_api_failure(self):
        class FailingGH:
            def fetch_user_created_at(self, username: str) -> str:
                raise RuntimeError("API down")

        sig = AccountAgeSignal(FailingGH(), DEFAULT_CONFIG, {"user": {"login": "x"}})
        assert sig.score() == 50.0

    def test_cross_repo_neutral_on_api_failure(self):
        class FailingGH:
            def search_prs_by_author(self, username: str, lookback_days: int = 7) -> int:
                raise RuntimeError("search down")

        sig = CrossRepoPRsSignal(FailingGH(), DEFAULT_CONFIG, {"user": {"login": "x"}})
        assert sig.score() == 50.0


# ---------------------------------------------------------------------------
# search URL must be properly percent-encoded
# ---------------------------------------------------------------------------

class TestSearchUrlEncoding:
    def test_query_string_is_url_encoded(self):
        captured: dict = {}

        class StubGH(GitHubClient):
            def __init__(self) -> None:
                pass  # bypass real init; we only override _rest_get

            def _rest_get(self, url, timeout=15):
                captured["url"] = url
                return {"total_count": 0}

        stub = StubGH()
        stub.search_prs_by_author("some user", lookback_days=7)

        url = captured["url"]
        assert url.startswith("https://api.github.com/search/issues?")
        assert "per_page=1" in url
        # No raw spaces / control chars anywhere (the original bug).
        assert " " not in url, url
        # ":" in "type:pr" must be percent-encoded, not left bare.
        assert "type%3Apr" in url, url
        # Round-trip: decoding the query recovers the original qualifiers.
        from urllib.parse import parse_qs
        decoded_q = parse_qs(url.split("?", 1)[1])["q"][0]
        assert decoded_q.startswith("type:pr author:some user created:>=")

    def test_label_names_in_urls_are_encoded(self):
        # A label with a space must survive being put in a URL path.
        calls: list = []

        class StubGH(GitHubClient):
            def __init__(self) -> None:
                self._rest_base = "https://api.github.com/repos/o/r"
                self.pr_number = 1

            def _rest_get(self, url, timeout=15):
                calls.append(("get", url))
                raise GitHubAPIError(404, "not found")

            def _rest_post(self, url, body, timeout=15):
                calls.append(("post", url, body))
                return {}

        stub = StubGH()
        stub._ensure_label_exists({"name": "needs review", "color": "fff", "description": ""})
        # The GET lookup URL must percent-encode the space.
        get_url = next(u for op, u in calls if op == "get")
        assert "needs%20review" in get_url


class TestGitHubClientPagination:
    def test_fetch_pr_files_paginates_past_100(self, monkeypatch):
        captured: list[str] = []

        def fake_urlopen(req, timeout=None):
            from urllib.parse import parse_qs, urlparse
            captured.append(req.full_url)
            page = parse_qs(urlparse(req.full_url).query)["page"][0]
            if page == "1":
                return _json_resp([{"filename": f"f{i}.py"} for i in range(100)])
            return _json_resp([{"filename": "f100.py"}])

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        gh = GitHubClient("tok", "o", "r", 1, None, {})
        files = gh.fetch_pr_files()

        assert len(files) == 101
        assert "per_page=100&page=1" in captured[0]
        assert "per_page=100&page=2" in captured[1]

    def test_fetch_pr_commits_paginates_past_100(self, monkeypatch):
        captured: list[str] = []

        def fake_urlopen(req, timeout=None):
            from urllib.parse import parse_qs, urlparse
            captured.append(req.full_url)
            page = parse_qs(urlparse(req.full_url).query)["page"][0]
            if page == "1":
                return _json_resp([{"sha": str(i)} for i in range(100)])
            return _json_resp([{"sha": "100"}])

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        gh = GitHubClient("tok", "o", "r", 1, None, {})
        commits = gh.fetch_pr_commits()

        assert len(commits) == 101
        assert "per_page=100&page=1" in captured[0]
        assert "per_page=100&page=2" in captured[1]


# ---------------------------------------------------------------------------
# contributor-trust signal tests
# ---------------------------------------------------------------------------

class TrustMockGH:
    """Configurable mock for the trust signals' GitHub API calls.

    Defaults describe a neutral / low-activity contributor; tests override
    only the attributes relevant to the signal under test.
    """

    def __init__(self, **kwargs):
        self.merged_in_repo = 0
        self.merged_total = 0
        self.closed_unmerged = 0
        self.issue_comments = 0
        self.engaged = 0
        self.authored = 0
        self.recent_titles: list[str] = []
        self.recent_activity: list[dict] = []
        self.user_profile: dict = {"bio": ""}
        # NLP-signal mock data
        self.repo_info: dict = {"description": "", "language": "", "topics": []}
        self.contributing_text: str | None = None
        self.llm_response: str = '{"score": 50}'
        self.llm_prompts: list[str] = []
        # repo-fit / maintainer-burden mock data
        self.file_texts: dict = {}            # path → text (None = missing)
        self.pr_files: list[dict] = []
        self.pr_commits: list[dict] = []
        self.repo_prs: list[dict] = []
        self.file_last_commit: dict = {}      # path → ISO date ("" = new file)
        for k, v in kwargs.items():
            setattr(self, k, v)

    def fetch_repo_info(self):
        return self.repo_info

    def fetch_file_text(self, path):
        # Per-path text if configured; else fall back to contributing_text so
        # the existing contribution_rules tests (which set contributing_text)
        # keep working.
        if path in self.file_texts:
            return self.file_texts[path]
        return self.contributing_text

    def fetch_pr_files(self):
        return self.pr_files

    def fetch_pr_commits(self):
        return self.pr_commits

    def fetch_repo_pr_titles(self, limit=50):
        return self.repo_prs

    def fetch_file_last_commit_date(self, path):
        return self.file_last_commit.get(path, "")

    def llm_judge(self, prompt, system=None):
        self.llm_prompts.append(prompt)
        return self.llm_response

    def fetch_user_profile(self, username):
        return self.user_profile

    def fetch_user_created_at(self, username):
        return self.user_profile.get("created_at", "")

    def search_prs_by_author(self, username, lookback_days=7):
        return 0

    def count_merged_prs_in_repo(self, username):
        return self.merged_in_repo

    def count_merged_prs(self, username):
        return self.merged_total

    def count_closed_unmerged_prs(self, username):
        return self.closed_unmerged

    def count_issue_comments_in_repo(self, username):
        return self.issue_comments

    def count_engaged_prs(self, username):
        return self.engaged

    def count_authored_prs(self, username):
        return self.authored

    def fetch_recent_pr_titles(self, username, limit=30):
        return self.recent_titles

    def fetch_recent_pr_activity(self, username, lookback_days=30, limit=30):
        return self.recent_activity


class TestAssociation:
    def test_owner_is_trusted(self):
        sig = AssociationSignal(None, DEFAULT_CONFIG, {"author_association": "OWNER"})
        assert sig.score() == 0.0

    def test_collaborator_is_trusted(self):
        sig = AssociationSignal(None, DEFAULT_CONFIG, {"author_association": "COLLABORATOR"})
        assert sig.score() == 0.0

    def test_contributor_low(self):
        sig = AssociationSignal(None, DEFAULT_CONFIG, {"author_association": "CONTRIBUTOR"})
        assert sig.score() == 15.0

    def test_first_time_high(self):
        sig = AssociationSignal(None, DEFAULT_CONFIG, {"author_association": "FIRST_TIME_CONTRIBUTOR"})
        assert sig.score() == 60.0

    def test_missing_defaults_high(self):
        sig = AssociationSignal(None, DEFAULT_CONFIG, {})
        assert sig.score() == 80.0


class TestRepoMergeHistory:
    def test_no_merged_prs(self):
        gh = TrustMockGH(merged_in_repo=0)
        sig = RepoMergeHistorySignal(gh, DEFAULT_CONFIG, {"user": {"login": "x"}})
        assert sig.score() == 80.0

    def test_many_merged(self):
        gh = TrustMockGH(merged_in_repo=20)
        sig = RepoMergeHistorySignal(gh, DEFAULT_CONFIG, {"user": {"login": "x"}})
        assert sig.score() < 10

    def test_unknown_user(self):
        sig = RepoMergeHistorySignal(None, DEFAULT_CONFIG, {})
        assert sig.score() == 50.0


class TestClosedUnmergedRatio:
    def test_mostly_rejected(self):
        gh = TrustMockGH(closed_unmerged=8, merged_total=2)
        sig = ClosedUnmergedRatioSignal(gh, DEFAULT_CONFIG, {"user": {"login": "x"}})
        assert sig.score() >= 80

    def test_mostly_merged(self):
        gh = TrustMockGH(closed_unmerged=1, merged_total=9)
        sig = ClosedUnmergedRatioSignal(gh, DEFAULT_CONFIG, {"user": {"login": "x"}})
        assert sig.score() < 30

    def test_no_closed_prs_neutral(self):
        gh = TrustMockGH(closed_unmerged=0, merged_total=0)
        sig = ClosedUnmergedRatioSignal(gh, DEFAULT_CONFIG, {"user": {"login": "x"}})
        assert sig.score() == 50.0


class TestIssueParticipation:
    def test_no_issue_participation(self):
        gh = TrustMockGH(issue_comments=0)
        sig = IssueParticipationSignal(gh, DEFAULT_CONFIG, {"user": {"login": "x"}})
        assert sig.score() == 70.0

    def test_active_discussion(self):
        gh = TrustMockGH(issue_comments=10)
        sig = IssueParticipationSignal(gh, DEFAULT_CONFIG, {"user": {"login": "x"}})
        assert sig.score() < 15


class TestReviewEngagement:
    def test_high_engagement(self):
        gh = TrustMockGH(engaged=9, authored=10)
        sig = ReviewEngagementSignal(gh, DEFAULT_CONFIG, {"user": {"login": "x"}})
        assert sig.score() < 20

    def test_no_engagement(self):
        gh = TrustMockGH(engaged=0, authored=10)
        sig = ReviewEngagementSignal(gh, DEFAULT_CONFIG, {"user": {"login": "x"}})
        assert sig.score() >= 70

    def test_no_prs_neutral(self):
        gh = TrustMockGH(engaged=0, authored=0)
        sig = ReviewEngagementSignal(gh, DEFAULT_CONFIG, {"user": {"login": "x"}})
        assert sig.score() == 50.0


class TestDuplicatePRTitles:
    def test_unique_titles(self):
        titles = [f"unique change {i}" for i in range(12)]
        gh = TrustMockGH(recent_titles=titles)
        sig = DuplicatePRTitlesSignal(gh, DEFAULT_CONFIG, {"user": {"login": "x"}})
        assert sig.score() <= 10

    def test_all_identical(self):
        gh = TrustMockGH(recent_titles=["bump deps"] * 5)
        sig = DuplicatePRTitlesSignal(gh, DEFAULT_CONFIG, {"user": {"login": "x"}})
        assert sig.score() > 80

    def test_too_few_prs(self):
        gh = TrustMockGH(recent_titles=["only one"])
        sig = DuplicatePRTitlesSignal(gh, DEFAULT_CONFIG, {"user": {"login": "x"}})
        assert sig.score() == 50.0


class TestBioPositioning:
    def test_generic_bio_no_work(self):
        gh = TrustMockGH(user_profile={"bio": "open source contributor"}, merged_total=0)
        sig = BioPositioningSignal(gh, DEFAULT_CONFIG, {"user": {"login": "x"}})
        assert sig.score() >= 60

    def test_generic_bio_with_work(self):
        gh = TrustMockGH(user_profile={"bio": "open source contributor"}, merged_total=20)
        sig = BioPositioningSignal(gh, DEFAULT_CONFIG, {"user": {"login": "x"}})
        assert sig.score() < 20

    def test_non_generic_bio(self):
        gh = TrustMockGH(user_profile={"bio": "I love cats"}, merged_total=0)
        sig = BioPositioningSignal(gh, DEFAULT_CONFIG, {"user": {"login": "x"}})
        assert sig.score() == 0.0

    def test_no_bio(self):
        gh = TrustMockGH(user_profile={"bio": ""}, merged_total=0)
        sig = BioPositioningSignal(gh, DEFAULT_CONFIG, {"user": {"login": "x"}})
        assert sig.score() == 0.0


class TestActivityBurstiness:
    @staticmethod
    def _activity(n: int, repos: int, span_days: int = 0):
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        return [
            {
                "created_at": (now - datetime.timedelta(days=span_days)).isoformat(),
                "repo": f"owner/repo{r}",
            }
            for r in (i % repos for i in range(n))
        ]

    def test_too_few(self):
        gh = TrustMockGH(recent_activity=self._activity(2, 1))
        sig = ActivityBurstinessSignal(gh, DEFAULT_CONFIG, {"user": {"login": "x"}})
        assert sig.score() == 10.0

    def test_bursty_and_broad(self):
        gh = TrustMockGH(recent_activity=self._activity(6, 3))
        sig = ActivityBurstinessSignal(gh, DEFAULT_CONFIG, {"user": {"login": "x"}})
        assert sig.score() == 90.0

    def test_broad_not_bursty(self):
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        acts = [
            {
                "created_at": (now - datetime.timedelta(days=d)).isoformat(),
                "repo": f"o/r{d}",
            }
            for d in range(6)
        ]
        gh = TrustMockGH(recent_activity=acts)
        sig = ActivityBurstinessSignal(gh, DEFAULT_CONFIG, {"user": {"login": "x"}})
        # 6 distinct repos → broad, but span > 24h → not bursty
        assert sig.score() == 35.0


@pytest.mark.parametrize("signal_cls", [
    RepoMergeHistorySignal,
    ClosedUnmergedRatioSignal,
    IssueParticipationSignal,
    ReviewEngagementSignal,
    DuplicatePRTitlesSignal,
    BioPositioningSignal,
    ActivityBurstinessSignal,
])
def test_trust_signal_neutral_on_api_failure(signal_cls):
    """Every trust signal that calls the API must degrade to 50 on failure."""

    class FailingGH:
        def __getattr__(self, name):
            def _raise(*a, **k):
                raise RuntimeError("API down")
            return _raise

    sig = signal_cls(FailingGH(), DEFAULT_CONFIG, {"user": {"login": "x"}})
    assert sig.score() == 50.0


# ---------------------------------------------------------------------------
# JSON extraction from LLM responses
# ---------------------------------------------------------------------------

class TestExtractFirstJson:
    def test_plain_object(self):
        assert extract_first_json('{"score": 42}') == {"score": 42}

    def test_fenced_json(self):
        assert extract_first_json('```json\n{"score": 42}\n```') == {"score": 42}

    def test_fenced_plain(self):
        assert extract_first_json('```\n{"score": 42}\n```') == {"score": 42}

    def test_prose_around(self):
        text = 'Sure! Here is the score:\n{"score": 42, "reason": "ok"}\nHope that helps.'
        assert extract_first_json(text)["score"] == 42

    def test_array(self):
        assert extract_first_json('prefix [1, 2, {"x": 3}] suffix') == [1, 2, {"x": 3}]

    def test_braces_inside_strings(self):
        # braces inside a string literal must not confuse the scanner
        text = '{"note": "a {fake} brace", "score": 7}'
        assert extract_first_json(text) == {"note": "a {fake} brace", "score": 7}

    def test_first_valid_block_wins(self):
        text = 'bad } not json, then {"score": 9} then {"score": 10}'
        assert extract_first_json(text)["score"] == 9

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            extract_first_json("")

    def test_no_json_raises(self):
        with pytest.raises(ValueError):
            extract_first_json("totally not json at all")


# ---------------------------------------------------------------------------
# LLM client (GitHub Models) — mocked HTTP
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._payload


def _json_resp(payload):
    return _FakeResp(json.dumps(payload).encode())


class TestLLMJudge:
    def test_returns_assistant_content_and_uses_config(self, monkeypatch):
        captured: dict = {}

        def fake_urlopen(req, timeout=None):
            captured["req"] = req
            return _FakeResp(json.dumps(
                {"choices": [{"message": {"content": '{"score": 42}'}}]}
            ).encode())

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        gh = GitHubClient("tok", "o", "r", 1, None, {"model": "m", "temperature": 0.0})
        assert gh.llm_judge("hi") == '{"score": 42}'

        body = json.loads(captured["req"].data)
        assert body["model"] == "m"
        assert body["temperature"] == 0.0
        assert body["max_tokens"] == 5000
        assert body["messages"] == [{"role": "user", "content": "hi"}]

    def test_prompt_is_capped_but_tail_instruction_survives(self, monkeypatch):
        captured: dict = {}

        def fake_urlopen(req, timeout=None):
            captured["data"] = json.loads(req.data)
            return _json_resp({"choices": [{"message": {"content": "ok"}}]})

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        gh = GitHubClient("tok", "o", "r", 1, None, {"max_input_tokens": 30})
        prompt = "a" * 200 + "RETURN JSON"
        gh.llm_judge(prompt)
        sent = captured["data"]["messages"][0]["content"]
        assert len(sent) <= 120
        assert "RETURN JSON" in sent

    def test_system_message_prepended(self, monkeypatch):
        captured: dict = {}

        def fake_urlopen(req, timeout=None):
            captured["data"] = json.loads(req.data)
            return _FakeResp(json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode())

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        gh = GitHubClient("tok", "o", "r", 1, None, {})
        gh.llm_judge("hi", system="be strict")
        msgs = captured["data"]["messages"]
        assert msgs[0] == {"role": "system", "content": "be strict"}
        assert msgs[1] == {"role": "user", "content": "hi"}

    def test_http_error_raises_github_api_error(self, monkeypatch):
        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 429, "Too Many Requests", {}, io.BytesIO(b"rate")
            )

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        gh = GitHubClient("tok", "o", "r", 1, None, {})
        with pytest.raises(GitHubAPIError) as ei:
            gh.llm_judge("hi")
        assert ei.value.status_code == 429

    def test_empty_choices_raises(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: _FakeResp(
            json.dumps({"choices": []}).encode()
        ))
        gh = GitHubClient("tok", "o", "r", 1, None, {})
        with pytest.raises(GitHubAPIError):
            gh.llm_judge("hi")


# ---------------------------------------------------------------------------
# related_work signal — provider: off / non_llm / llm
# ---------------------------------------------------------------------------

def _cfg_related(provider, llm=False):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg.setdefault("signals", {}).setdefault("related_work", {})["provider"] = provider
    cfg.setdefault("llm", {})["enabled"] = llm
    return cfg


class TestRelatedWork:
    PR = {"user": {"login": "x"}}

    def test_off_is_skipped(self):
        sig = RelatedWorkSignal(TrustMockGH(), _cfg_related("off"), self.PR)
        assert sig.score() is None

    def test_llm_provider_without_llm_enabled_is_skipped(self):
        sig = RelatedWorkSignal(TrustMockGH(), _cfg_related("llm", llm=False), self.PR)
        assert sig.score() is None

    def test_yaml_off_coercion_tolerated(self):
        # YAML 1.1 (PyYAML) parses bare `off` as boolean False; must still
        # mean "off" (skip), not "unknown provider".
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg.setdefault("signals", {}).setdefault("related_work", {})["provider"] = False
        sig = RelatedWorkSignal(TrustMockGH(), cfg, self.PR)
        assert sig.score() is None

    def test_unknown_provider_skipped(self):
        sig = RelatedWorkSignal(TrustMockGH(), _cfg_related("magic"), self.PR)
        assert sig.score() is None

    def test_non_llm_unrelated(self):
        gh = TrustMockGH(
            repo_info={"description": "kubernetes cluster controller",
                       "language": "Go", "topics": ["kubernetes", "containers"]},
            recent_titles=["fix typo in readme", "bump version"],
        )
        sig = RelatedWorkSignal(gh, _cfg_related("non_llm"), self.PR)
        assert sig.score() == 70.0   # zero overlap → unrelated

    def test_non_llm_related(self):
        gh = TrustMockGH(
            repo_info={"description": "python web framework",
                       "language": "Python", "topics": ["python", "web", "framework"]},
            recent_titles=["add python web framework route", "fix python web handler"],
        )
        sig = RelatedWorkSignal(gh, _cfg_related("non_llm"), self.PR)
        assert sig.score() == 15.0   # high overlap → trusted

    def test_llm_parses_json_score(self):
        gh = TrustMockGH(
            repo_info={"description": "a repo", "language": "Python", "topics": ["x"]},
            recent_titles=["some pr"],
            llm_response='{"score": 30}',
        )
        sig = RelatedWorkSignal(gh, _cfg_related("llm", llm=True), self.PR)
        assert sig.score() == 30.0

    def test_llm_tolerates_prose_and_fences(self):
        gh = TrustMockGH(
            repo_info={"description": "a repo", "language": "Python", "topics": ["x"]},
            recent_titles=["some pr"],
            llm_response='Sure!\n```json\n{"score": 88}\n```\nDone.',
        )
        sig = RelatedWorkSignal(gh, _cfg_related("llm", llm=True), self.PR)
        assert sig.score() == 88.0

    def test_llm_invalid_json_falls_back_to_neutral(self):
        gh = TrustMockGH(
            repo_info={"description": "a repo", "language": "Python", "topics": ["x"]},
            recent_titles=["some pr"],
            llm_response="not json at all",
        )
        sig = RelatedWorkSignal(gh, _cfg_related("llm", llm=True), self.PR)
        assert sig.score() == 50.0

    def test_llm_out_of_range_score_clamped(self):
        gh = TrustMockGH(
            repo_info={"description": "a repo", "language": "Python", "topics": ["x"]},
            recent_titles=["some pr"],
            llm_response='{"score": 9999}',
        )
        sig = RelatedWorkSignal(gh, _cfg_related("llm", llm=True), self.PR)
        assert sig.score() == 100.0


# ---------------------------------------------------------------------------
# contribution_rules signal — provider: off / llm (non_llm unsupported)
# ---------------------------------------------------------------------------

def _cfg_rules(provider, llm=False):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg.setdefault("signals", {}).setdefault("contribution_rules", {})["provider"] = provider
    cfg.setdefault("llm", {})["enabled"] = llm
    return cfg


class TestContributionRules:
    PR = {"user": {"login": "x"}, "title": "Fix bug", "body": "Fixes #1 by doing X"}

    def test_off_is_skipped(self):
        sig = ContributionRulesSignal(TrustMockGH(), _cfg_rules("off"), self.PR)
        assert sig.score() is None

    def test_non_llm_unsupported_is_skipped(self):
        sig = ContributionRulesSignal(TrustMockGH(), _cfg_rules("non_llm", llm=False), self.PR)
        assert sig.score() is None

    def test_llm_without_rules_file_is_skipped(self):
        gh = TrustMockGH(contributing_text=None)
        sig = ContributionRulesSignal(gh, _cfg_rules("llm", llm=True), self.PR)
        assert sig.score() is None

    def test_llm_parses_score(self):
        gh = TrustMockGH(
            contributing_text="Please add tests and describe your change.",
            llm_response='{"score": 80, "reason": "no tests"}',
        )
        sig = ContributionRulesSignal(gh, _cfg_rules("llm", llm=True), self.PR)
        assert sig.score() == 80.0

    def test_llm_tolerates_fenced_json(self):
        gh = TrustMockGH(
            contributing_text="Please add tests.",
            llm_response='```json\n{"score": 25}\n```',
        )
        sig = ContributionRulesSignal(gh, _cfg_rules("llm", llm=True), self.PR)
        assert sig.score() == 25.0

    def test_llm_prompt_includes_bounded_patch_context(self):
        gh = TrustMockGH(
            contributing_text="All public functions need type hints.",
            pr_files=[
                {"filename": "src/a.py", "status": "modified", "additions": 5, "deletions": 1,
                 "patch": "@@ def add(a, b):\n+def add(a: int, b: int) -> int:"},
            ],
            llm_response='{"score": 10}',
        )
        sig = ContributionRulesSignal(gh, _cfg_rules("llm", llm=True), self.PR)
        assert sig.score() == 10.0
        assert "Selected PR patches" in gh.llm_prompts[-1]
        assert "src/a.py" in gh.llm_prompts[-1]


def _cfg_diff(provider, llm=False, **overrides):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg.setdefault("signals", {}).setdefault("diff_credibility", {})["provider"] = provider
    cfg.setdefault("llm", {})["enabled"] = llm
    cfg["signals"]["diff_credibility"].update(overrides)
    return cfg


class TestDiffCredibility:
    PR = {"title": "Improve router performance", "body": "Speeds up route lookup."}

    def test_off_is_skipped(self):
        sig = DiffCredibilitySignal(TrustMockGH(), _cfg_diff("off"), self.PR)
        assert sig.score() is None

    def test_non_llm_unsupported_is_skipped(self):
        sig = DiffCredibilitySignal(TrustMockGH(), _cfg_diff("non_llm", llm=False), self.PR)
        assert sig.score() is None

    def test_no_patch_context_is_skipped(self):
        sig = DiffCredibilitySignal(TrustMockGH(pr_files=[{"filename": "image.png"}]),
                                    _cfg_diff("llm", llm=True), self.PR)
        assert sig.score() is None

    def test_llm_uses_top_addition_patches_and_parses_score(self):
        gh = TrustMockGH(
            pr_files=[
                {"filename": "small.py", "status": "modified", "additions": 1, "deletions": 0,
                 "patch": "@@ small"},
                {"filename": "large.py", "status": "modified", "additions": 10, "deletions": 2,
                 "patch": "@@ large"},
                {"filename": "medium.py", "status": "modified", "additions": 5, "deletions": 1,
                 "patch": "@@ medium"},
                {"filename": "tiny.py", "status": "modified", "additions": 0, "deletions": 1,
                 "patch": "@@ tiny"},
            ],
            llm_response='{"score": 77}',
        )
        sig = DiffCredibilitySignal(gh, _cfg_diff("llm", llm=True), self.PR)
        assert sig.score() == 77.0
        prompt = gh.llm_prompts[-1]
        assert "large.py" in prompt
        assert "medium.py" in prompt
        assert "small.py" in prompt
        assert "tiny.py" not in prompt


# ---------------------------------------------------------------------------
# repo-fit & maintainer-burden signals
# ---------------------------------------------------------------------------

def _files(*names):
    """Build a pr_files list of {filename} dicts."""
    return [{"filename": n} for n in names]


# ---- pr_template ----------------------------------------------------------

def _cfg_template(provider, llm=False, **overrides):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg.setdefault("signals", {}).setdefault("pr_template", {})["provider"] = provider
    cfg.setdefault("llm", {})["enabled"] = llm
    cfg["signals"]["pr_template"].update(overrides)
    return cfg


TEMPLATE = "## Summary\n${SUMMARY}\n## Why\n${WHY}"
TEMPLATE_SECTIONS = "## Summary\n## Why"


class TestPrTemplate:
    def test_off_is_skipped(self):
        sig = PrTemplateSignal(TrustMockGH(), _cfg_template("off"), {})
        assert sig.score() is None

    def test_llm_without_llm_enabled_is_skipped(self):
        sig = PrTemplateSignal(TrustMockGH(), _cfg_template("llm", llm=False), {})
        assert sig.score() is None

    def test_no_template_file_is_skipped(self):
        sig = PrTemplateSignal(TrustMockGH(), _cfg_template("non_llm"), {})
        assert sig.score() is None

    def test_non_llm_empty_body_is_high(self):
        gh = TrustMockGH(file_texts={".github/PULL_REQUEST_TEMPLATE.md": TEMPLATE})
        sig = PrTemplateSignal(gh, _cfg_template("non_llm"), {"body": ""})
        assert sig.score() == 80.0

    def test_non_llm_all_placeholders_unfilled(self):
        gh = TrustMockGH(file_texts={".github/PULL_REQUEST_TEMPLATE.md": TEMPLATE})
        body = "## Summary\n${SUMMARY}\n## Why\n${WHY}"
        sig = PrTemplateSignal(gh, _cfg_template("non_llm"), {"body": body})
        assert sig.score() == 80.0   # ratio == 1 → 80

    def test_non_llm_all_placeholders_filled_is_low(self):
        gh = TrustMockGH(file_texts={".github/PULL_REQUEST_TEMPLATE.md": TEMPLATE})
        body = "## Summary\nfixed the crash\n## Why\nnull deref"
        sig = PrTemplateSignal(gh, _cfg_template("non_llm"), {"body": body})
        assert sig.score() == 10.0   # filled + headers present

    def test_non_llm_filled_but_template_not_used_is_medium(self):
        gh = TrustMockGH(file_texts={".github/PULL_REQUEST_TEMPLATE.md": TEMPLATE})
        # placeholder removed but the template's section headers are absent too
        sig = PrTemplateSignal(gh, _cfg_template("non_llm"), {"body": "did some stuff"})
        assert sig.score() == 40.0

    def test_non_llm_sections_only_filled(self):
        gh = TrustMockGH(file_texts={".github/PULL_REQUEST_TEMPLATE.md": TEMPLATE_SECTIONS})
        body = "## Summary\nx\n## Why\ny"
        sig = PrTemplateSignal(gh, _cfg_template("non_llm"), {"body": body})
        assert sig.score() == 10.0

    def test_non_llm_sections_only_all_missing(self):
        gh = TrustMockGH(file_texts={".github/PULL_REQUEST_TEMPLATE.md": TEMPLATE_SECTIONS})
        sig = PrTemplateSignal(gh, _cfg_template("non_llm"), {"body": "random prose"})
        assert sig.score() == 60.0

    def test_non_llm_prose_only_template_is_skipped(self):
        gh = TrustMockGH(file_texts={".github/PULL_REQUEST_TEMPLATE.md": "just some prose"})
        sig = PrTemplateSignal(gh, _cfg_template("non_llm"), {"body": "x"})
        assert sig.score() is None

    def test_custom_template_path(self):
        gh = TrustMockGH(file_texts={"docs/pr_template.md": TEMPLATE})
        cfg = _cfg_template("non_llm", template_path="docs/pr_template.md")
        sig = PrTemplateSignal(gh, cfg, {"body": "## Summary\n${SUMMARY}\n## Why\n${WHY}"})
        assert sig.score() == 80.0

    def test_llm_parses_score(self):
        gh = TrustMockGH(
            file_texts={".github/PULL_REQUEST_TEMPLATE.md": TEMPLATE},
            llm_response='{"score": 30}',
        )
        sig = PrTemplateSignal(gh, _cfg_template("llm", llm=True), {"body": "hi"})
        assert sig.score() == 30.0

    def test_llm_invalid_json_falls_back_to_neutral(self):
        gh = TrustMockGH(
            file_texts={".github/PULL_REQUEST_TEMPLATE.md": TEMPLATE},
            llm_response="not json",
        )
        sig = PrTemplateSignal(gh, _cfg_template("llm", llm=True), {"body": "hi"})
        assert sig.score() == 50.0


# ---- scope_alignment ------------------------------------------------------

def _cfg_scope(provider, llm=False, **overrides):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg.setdefault("signals", {}).setdefault("scope_alignment", {})["provider"] = provider
    cfg.setdefault("llm", {})["enabled"] = llm
    cfg["signals"]["scope_alignment"].update(overrides)
    return cfg


class TestScopeAlignment:
    def test_off_is_skipped(self):
        sig = ScopeAlignmentSignal(TrustMockGH(), _cfg_scope("off"), {})
        assert sig.score() is None

    def test_no_docs_is_skipped(self):
        sig = ScopeAlignmentSignal(TrustMockGH(), _cfg_scope("non_llm"), {})
        assert sig.score() is None

    def test_non_llm_aligned_is_low(self):
        gh = TrustMockGH(file_texts={"ROADMAP.md": "python web framework routing"})
        pr = {"title": "add python web framework routing", "body": ""}
        sig = ScopeAlignmentSignal(gh, _cfg_scope("non_llm"), pr)
        assert sig.score() == 15.0   # high overlap → trusted

    def test_non_llm_misaligned_is_high(self):
        gh = TrustMockGH(file_texts={"ROADMAP.md": "kubernetes cluster controller"})
        pr = {"title": "bump version number", "body": ""}
        sig = ScopeAlignmentSignal(gh, _cfg_scope("non_llm"), pr)
        assert sig.score() == 65.0   # no overlap → misaligned

    def test_custom_reference_docs(self):
        gh = TrustMockGH(file_texts={"docs/scope.md": "python web framework"})
        cfg = _cfg_scope("non_llm", reference_docs=["docs/scope.md"])
        pr = {"title": "python web framework fix", "body": ""}
        sig = ScopeAlignmentSignal(gh, cfg, pr)
        assert sig.score() == 15.0

    def test_missing_reference_doc_is_skipped(self):
        # Only a non-existent doc configured → skip.
        gh = TrustMockGH(file_texts={})
        cfg = _cfg_scope("non_llm", reference_docs=["DOES_NOT_EXIST.md"])
        sig = ScopeAlignmentSignal(gh, cfg, {"title": "x", "body": ""})
        assert sig.score() is None

    def test_llm_parses_score(self):
        gh = TrustMockGH(
            file_texts={"ROADMAP.md": "build a web framework"},
            llm_response='{"score": 20}',
        )
        sig = ScopeAlignmentSignal(gh, _cfg_scope("llm", llm=True), {"title": "x", "body": ""})
        assert sig.score() == 20.0

    def test_llm_prompt_includes_patch_context(self):
        gh = TrustMockGH(
            file_texts={"ROADMAP.md": "build a web framework"},
            pr_files=[
                {"filename": "src/router.py", "status": "modified", "additions": 4, "deletions": 1,
                 "patch": "@@ class Router:\n+    def route(self): pass"},
            ],
            llm_response='{"score": 20}',
        )
        sig = ScopeAlignmentSignal(gh, _cfg_scope("llm", llm=True), {"title": "router", "body": ""})
        assert sig.score() == 20.0
        assert "Selected PR patches" in gh.llm_prompts[-1]
        assert "src/router.py" in gh.llm_prompts[-1]


# ---- pr_body_quality ------------------------------------------------------

def _cfg_body(provider, llm=False, **overrides):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg.setdefault("signals", {}).setdefault("pr_body_quality", {})["provider"] = provider
    cfg.setdefault("llm", {})["enabled"] = llm
    cfg["signals"]["pr_body_quality"].update(overrides)
    return cfg


class TestPrBodyQuality:
    def test_off_is_skipped(self):
        sig = PrBodyQualitySignal(TrustMockGH(), _cfg_body("off"), {})
        assert sig.score() is None

    def test_non_llm_empty_body(self):
        sig = PrBodyQualitySignal(TrustMockGH(), _cfg_body("non_llm"), {"body": ""})
        assert sig.score() == 80.0

    def test_non_llm_short_body(self):
        sig = PrBodyQualitySignal(TrustMockGH(), _cfg_body("non_llm"), {"body": "fix"})
        assert sig.score() == 55.0

    def test_non_llm_substantial_body_no_vague(self):
        body = (
            "This PR fixes the null-pointer crash in the router that occurred "
            "whenever an incoming request carried no body. The router "
            "dereferenced the headers map before checking for None. I added an "
            "early-return guard in Router.dispatch and a regression test in "
            "tests/test_router.py that posts an empty body and asserts a 200 "
            "response instead of a 500. Verified locally with pytest; the full "
            "suite still passes."
        )
        sig = PrBodyQualitySignal(TrustMockGH(), _cfg_body("non_llm"), {"body": body})
        assert sig.score() == 15.0

    def test_non_llm_vague_phrases_boost(self):
        body = (
            "This change improves maintainability and enhances performance by "
            "following best practices across the codebase. The refactor "
            "extracts repeated logic into a shared helper, removes duplicated "
            "sections, and reorganizes the module layout so future edits are "
            "easier to reason about and less error-prone for contributors."
        )
        sig = PrBodyQualitySignal(TrustMockGH(), _cfg_body("non_llm"), {"body": body})
        # base 15 (substantial) + 3 vague hits * 12 = 51
        assert sig.score() == 51.0

    def test_custom_vague_phrases(self):
        body = "this is flurg the blarg" * 5
        cfg = _cfg_body("non_llm", vague_phrases=["flurg"])
        sig = PrBodyQualitySignal(TrustMockGH(), cfg, {"body": body})
        assert sig.score() > 15.0

    def test_llm_parses_score(self):
        gh = TrustMockGH(llm_response='{"score": 90}')
        sig = PrBodyQualitySignal(gh, _cfg_body("llm", llm=True), {"body": "x"})
        assert sig.score() == 90.0


# ---- tests_included -------------------------------------------------------

class TestTestsIncluded:
    def test_code_with_tests_is_low(self):
        gh = TrustMockGH(pr_files=_files("src/app.py", "tests/test_app.py"))
        sig = TestsIncludedSignal(gh, DEFAULT_CONFIG, {})
        assert sig.score() == 15.0

    def test_tests_only_is_strongly_low(self):
        gh = TrustMockGH(pr_files=_files("tests/test_app.py"))
        sig = TestsIncludedSignal(gh, DEFAULT_CONFIG, {})
        assert sig.score() == 5.0

    def test_no_tests_small_change(self):
        gh = TrustMockGH(pr_files=_files("src/app.py"))
        sig = TestsIncludedSignal(gh, DEFAULT_CONFIG, {"additions": 10, "deletions": 0})
        assert sig.score() == 40.0

    def test_no_tests_large_change(self):
        gh = TrustMockGH(pr_files=_files("src/app.py"))
        sig = TestsIncludedSignal(gh, DEFAULT_CONFIG, {"additions": 1000, "deletions": 0})
        assert sig.score() > 60.0

    def test_empty_files_neutral(self):
        gh = TrustMockGH(pr_files=[])
        sig = TestsIncludedSignal(gh, DEFAULT_CONFIG, {})
        assert sig.score() == 50.0

    def test_api_failure_neutral(self):
        class Fail:
            def fetch_pr_files(self):
                raise RuntimeError("down")
        sig = TestsIncludedSignal(Fail(), DEFAULT_CONFIG, {})
        assert sig.score() == 50.0


# ---- change_scope ---------------------------------------------------------

class TestChangeScope:
    def test_focused_single_dir(self):
        gh = TrustMockGH(pr_files=_files("src/a.py", "src/b.py"))
        sig = ChangeScopeSignal(gh, DEFAULT_CONFIG, {})
        assert sig.score() == 10.0

    def test_sprawling_many_dirs(self):
        gh = TrustMockGH(pr_files=_files("a/x.py", "b/y.py", "c/z.py", "d/w.py", "e/v.py", "f/u.py"))
        sig = ChangeScopeSignal(gh, DEFAULT_CONFIG, {})
        assert sig.score() > 80.0   # 6 dirs → 65 + (6-4)*8 = 81

    def test_api_failure_neutral(self):
        class Fail:
            def fetch_pr_files(self):
                raise RuntimeError("down")
        sig = ChangeScopeSignal(Fail(), DEFAULT_CONFIG, {})
        assert sig.score() == 50.0


# ---- risky_paths ----------------------------------------------------------

class TestRiskyPaths:
    def test_no_risky_paths(self):
        gh = TrustMockGH(pr_files=_files("src/app.py", "src/util.py"))
        sig = RiskyPathsSignal(gh, DEFAULT_CONFIG, {})
        assert sig.score() == 5.0

    def test_single_risky_group(self):
        gh = TrustMockGH(pr_files=_files("src/app.py", "package.json"))
        sig = RiskyPathsSignal(gh, DEFAULT_CONFIG, {})
        assert sig.score() == 30.0   # 1 group → linear(1,0,2,5,55)

    def test_many_risky_groups(self):
        gh = TrustMockGH(pr_files=_files(
            "package.json",            # dependencies
            "db/migrations/0001.sql",  # migrations
            ".github/workflows/ci.yml",# ci_build_deploy
            "src/auth/login.py",       # security_auth
        ))
        sig = RiskyPathsSignal(gh, DEFAULT_CONFIG, {})
        assert sig.score() >= 75.0

    def test_api_failure_neutral(self):
        class Fail:
            def fetch_pr_files(self):
                raise RuntimeError("down")
        sig = RiskyPathsSignal(Fail(), DEFAULT_CONFIG, {})
        assert sig.score() == 50.0


# ---- file_maintenance -----------------------------------------------------

class TestFileMaintenance:
    def test_only_source_files_is_low(self):
        gh = TrustMockGH(pr_files=_files("src/app.py", "src/util.py"))
        sig = FileMaintenanceSignal(gh, DEFAULT_CONFIG, {})
        assert sig.score() == 5.0

    def test_vendored_files_raise_score(self):
        gh = TrustMockGH(pr_files=_files("src/app.py", "vendor/lib/a.py"))
        sig = FileMaintenanceSignal(gh, DEFAULT_CONFIG, {})
        assert sig.score() > 5.0

    def test_generated_files_raise_score(self):
        gh = TrustMockGH(pr_files=_files("src/app.py", "src/gen.pb.go"))
        sig = FileMaintenanceSignal(gh, DEFAULT_CONFIG, {})
        assert sig.score() > 5.0

    def test_recency_default_off(self):
        gh = TrustMockGH(pr_files=_files("src/app.py"))
        sig = FileMaintenanceSignal(gh, DEFAULT_CONFIG, {})
        assert sig.score() == 5.0   # no recency call expected

    def test_recency_stale_boosts(self):
        import datetime
        old = (datetime.datetime.now(datetime.timezone.utc)
               - datetime.timedelta(days=400)).isoformat()
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["signals"]["file_maintenance"]["check_recency"] = True
        gh = TrustMockGH(
            pr_files=_files("src/app.py"),
            file_last_commit={"src/app.py": old},
        )
        sig = FileMaintenanceSignal(gh, cfg, {})
        # base 5 + 40*1.0 (all stale) = 45
        assert sig.score() == 45.0

    def test_recency_new_file_not_stale(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["signals"]["file_maintenance"]["check_recency"] = True
        gh = TrustMockGH(
            pr_files=_files("src/app.py"),
            file_last_commit={"src/app.py": ""},   # newly added
        )
        sig = FileMaintenanceSignal(gh, cfg, {})
        assert sig.score() == 5.0   # new file → not stale

    def test_api_failure_neutral(self):
        class Fail:
            def fetch_pr_files(self):
                raise RuntimeError("down")
        sig = FileMaintenanceSignal(Fail(), DEFAULT_CONFIG, {})
        assert sig.score() == 50.0


# ---- linked_issue ---------------------------------------------------------

class TestLinkedIssue:
    def test_hashes_reference(self):
        sig = LinkedIssueSignal(None, DEFAULT_CONFIG, {"title": "Fix bug", "body": "Fixes #123"})
        assert sig.score() == 10.0

    def test_bare_hash_reference(self):
        sig = LinkedIssueSignal(None, DEFAULT_CONFIG, {"title": "Fix #42", "body": ""})
        assert sig.score() == 10.0

    def test_issue_url_reference(self):
        sig = LinkedIssueSignal(None, DEFAULT_CONFIG,
                                {"title": "x", "body": "see https://github.com/o/r/issues/7"})
        assert sig.score() == 10.0

    def test_no_reference_small(self):
        sig = LinkedIssueSignal(None, DEFAULT_CONFIG,
                                {"title": "typo", "body": "fix typo", "additions": 2, "deletions": 2})
        assert sig.score() == 30.0

    def test_no_reference_large(self):
        sig = LinkedIssueSignal(None, DEFAULT_CONFIG,
                                {"title": "x", "body": "", "additions": 500, "deletions": 0})
        assert sig.score() == 55.0

    def test_markdown_heading_not_a_reference(self):
        # "# Heading" has no digit after # → not an issue reference.
        sig = LinkedIssueSignal(None, DEFAULT_CONFIG,
                                {"title": "# Notes", "body": "some text", "additions": 500, "deletions": 0})
        assert sig.score() == 55.0


# ---- duplicate_work -------------------------------------------------------

class TestDuplicateWork:
    PR = {"title": "Add dark mode", "number": 42}

    def test_no_existing_prs(self):
        gh = TrustMockGH(repo_prs=[])
        sig = DuplicateWorkSignal(gh, DEFAULT_CONFIG, self.PR)
        assert sig.score() == 5.0

    def test_exact_match_open_pr(self):
        gh = TrustMockGH(repo_prs=[
            {"title": "add dark mode", "state": "open", "number": 7},
        ])
        sig = DuplicateWorkSignal(gh, DEFAULT_CONFIG, self.PR)
        assert sig.score() == 85.0

    def test_exact_match_closed_pr(self):
        gh = TrustMockGH(repo_prs=[
            {"title": "Add Dark Mode!", "state": "closed", "number": 7},
        ])
        sig = DuplicateWorkSignal(gh, DEFAULT_CONFIG, self.PR)
        assert sig.score() == 60.0   # punctuation/case normalized away

    def test_no_match_different_title(self):
        gh = TrustMockGH(repo_prs=[
            {"title": "Fix login bug", "state": "open", "number": 7},
        ])
        sig = DuplicateWorkSignal(gh, DEFAULT_CONFIG, self.PR)
        assert sig.score() == 5.0

    def test_excludes_current_pr(self):
        # Same number as the PR under review must not count as a duplicate.
        gh = TrustMockGH(repo_prs=[
            {"title": "Add dark mode", "state": "open", "number": 42},
        ])
        sig = DuplicateWorkSignal(gh, DEFAULT_CONFIG, self.PR)
        assert sig.score() == 5.0

    def test_api_failure_neutral(self):
        class Fail:
            def fetch_repo_pr_titles(self, limit=50):
                raise RuntimeError("down")
        sig = DuplicateWorkSignal(Fail(), DEFAULT_CONFIG, self.PR)
        assert sig.score() == 50.0


# ---- signoff --------------------------------------------------------------

def _cfg_signoff(required):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg.setdefault("signals", {}).setdefault("signoff", {})["required"] = required
    return cfg


class TestSignoff:
    def test_default_not_required_is_skipped(self):
        sig = SignoffSignal(TrustMockGH(pr_commits=[]), DEFAULT_CONFIG, {})
        assert sig.score() is None

    def test_required_all_signed(self):
        gh = TrustMockGH(pr_commits=[
            {"commit": {"message": "fix bug\n\nSigned-off-by: Alice <a@x.com>"}},
        ])
        sig = SignoffSignal(gh, _cfg_signoff(True), {})
        assert sig.score() == 0.0

    def test_required_none_signed(self):
        gh = TrustMockGH(pr_commits=[
            {"commit": {"message": "fix bug"}},
            {"commit": {"message": "more"}},
        ])
        sig = SignoffSignal(gh, _cfg_signoff(True), {})
        assert sig.score() == 85.0

    def test_required_some_signed(self):
        gh = TrustMockGH(pr_commits=[
            {"commit": {"message": "fix bug\n\nSigned-off-by: Alice <a@x.com>"}},
            {"commit": {"message": "no signoff here"}},
        ])
        sig = SignoffSignal(gh, _cfg_signoff(True), {})
        assert sig.score() == 62.5

    def test_required_mostly_unsigned_scores_near_none_signed(self):
        gh = TrustMockGH(pr_commits=[
            {"commit": {"message": "signed\n\nSigned-off-by: Alice <a@x.com>"}},
            *({"commit": {"message": f"unsigned {i}"}} for i in range(9)),
        ])
        sig = SignoffSignal(gh, _cfg_signoff(True), {})
        assert sig.score() == 80.5

    def test_api_failure_neutral(self):
        class Fail:
            def fetch_pr_commits(self):
                raise RuntimeError("down")
        sig = SignoffSignal(Fail(), _cfg_signoff(True), {})
        assert sig.score() == 50.0


# ---------------------------------------------------------------------------
# scorer skip: None scores are excluded from the weighted average
# ---------------------------------------------------------------------------

class TestScorerSkip:
    def test_none_scores_excluded_from_average(self):
        active = MagicMock()
        active.name.return_value = "a"
        active.score.return_value = 100.0
        skipped = MagicMock()
        skipped.name.return_value = "b"
        skipped.score.return_value = None
        cfg = {"weights": {"a": 1.0, "b": 1.0}}
        score, breakdown = compute_spam_score([active, skipped], cfg)
        assert score == 100.0     # only 'a' counts (1×100 / 1)
        names = {b["name"]: b for b in breakdown}
        assert names["a"]["weighted"] == 100.0
        assert names["b"]["raw"] is None
        assert names["b"]["weighted"] is None
