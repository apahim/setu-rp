"""Tests for the analysis layer."""

from datetime import datetime

import pytest

from setu_rp.analysis.contributors import classify_contributor
from setu_rp.analysis.metrics_rq1 import (
    compute_rejection,
    compute_review_iterations,
    compute_time_to_first_feedback,
    compute_time_to_first_human_feedback,
    compute_time_to_first_human_review,
    compute_time_to_merge,
)
from setu_rp.analysis.metrics_rq2 import count_comments_by_type
from setu_rp.analysis.schema import init_analysis_tables
from setu_rp.analysis.statistics import (
    benjamini_hochberg,
    check_normality,
    choose_and_run_test,
    cohens_d,
    descriptive_stats,
    run_proportion_test,
    wilson_ci,
)
from setu_rp.analysis.time_windows import (
    TimeWindow,
    classify_pr_period,
    compute_time_windows,
)


@pytest.fixture
def analysis_db(db_conn):
    """DB connection with both collection and analysis schemas initialized."""
    init_analysis_tables(db_conn)
    return db_conn


@pytest.fixture
def seeded_db(analysis_db):
    """DB with sample data for testing."""
    conn = analysis_db

    # Insert repository with bot adoption date
    conn.execute(
        "INSERT INTO repositories (id, owner, name, full_name, bot_adoption_date, fetched_at) "
        "VALUES (1, 'openshift', 'hypershift', 'openshift/hypershift', '2024-06-01T00:00:00', '2024-01-01')"
    )

    # Insert users
    conn.execute(
        "INSERT INTO users (id, login, type, name, fetched_at) VALUES "
        "(1, 'human1', 'User', 'Human One', '2024-01-01')"
    )
    conn.execute(
        "INSERT INTO users (id, login, type, name, fetched_at) VALUES "
        "(2, 'bot1', 'Bot', 'Bot One', '2024-01-01')"
    )
    conn.execute(
        "INSERT INTO users (id, login, type, name, fetched_at) VALUES "
        "(3, 'human2', 'User', 'Human Two', '2024-01-01')"
    )

    # Insert PRs: 2 pre-period, 2 post-period
    prs = [
        (101, 1, "closed", 1, "2024-04-01T10:00:00", "2024-04-02T10:00:00",
         "2024-04-02T10:00:00", "2024-04-02T10:00:00", 10, 5, 2),
        (102, 2, "closed", 1, "2024-05-01T10:00:00", "2024-05-02T10:00:00",
         "2024-05-02T10:00:00", None, 20, 10, 3),
        (103, 3, "closed", 3, "2024-07-01T10:00:00", "2024-07-02T10:00:00",
         "2024-07-02T10:00:00", "2024-07-02T10:00:00", 15, 8, 4),
        (104, 4, "open", 1, "2024-08-01T10:00:00", "2024-08-02T10:00:00",
         None, None, 5, 2, 1),
    ]
    for pr in prs:
        conn.execute(
            "INSERT INTO pull_requests (id, number, state, author_id, created_at, updated_at, "
            "closed_at, merged_at, additions, deletions, changed_files, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '2024-01-01')",
            pr,
        )

    # Insert reviews
    conn.execute(
        "INSERT INTO reviews (id, pull_request_id, reviewer_id, state, body, submitted_at, fetched_at) "
        "VALUES (1001, 101, 3, 'APPROVED', 'LGTM', '2024-04-01T12:00:00', '2024-01-01')"
    )
    conn.execute(
        "INSERT INTO reviews (id, pull_request_id, reviewer_id, state, body, submitted_at, fetched_at) "
        "VALUES (1002, 101, 3, 'CHANGES_REQUESTED', 'Fix this', '2024-04-01T11:00:00', '2024-01-01')"
    )

    # Insert review comments
    conn.execute(
        "INSERT INTO review_comments (id, pull_request_id, review_id, author_id, body, path, created_at, updated_at, fetched_at) "
        "VALUES (2001, 101, 1001, 3, 'Good code', 'main.py', '2024-04-01T12:30:00', '2024-04-01T12:30:00', '2024-01-01')"
    )
    conn.execute(
        "INSERT INTO review_comments (id, pull_request_id, review_id, author_id, body, path, created_at, updated_at, fetched_at) "
        "VALUES (2002, 101, 1001, 2, 'Bot comment', 'main.py', '2024-04-01T11:30:00', '2024-04-01T11:30:00', '2024-01-01')"
    )

    # Insert issue comments
    conn.execute(
        "INSERT INTO issue_comments (id, pull_request_id, author_id, body, created_at, updated_at, fetched_at) "
        "VALUES (3001, 101, 3, 'Please update', '2024-04-01T13:00:00', '2024-04-01T13:00:00', '2024-01-01')"
    )

    conn.commit()
    return conn


# --- Time Windows ---

class TestTimeWindows:
    def test_compute_time_windows(self, seeded_db):
        window = compute_time_windows(seeded_db, 3, 3)
        assert window.bot_adoption_date == datetime(2024, 6, 1)
        assert window.pre_start == datetime(2024, 3, 1)
        assert window.pre_end == datetime(2024, 6, 1)
        assert window.post_start == datetime(2024, 6, 1)
        assert window.post_end == datetime(2024, 9, 1)

    def test_compute_time_windows_with_override(self, seeded_db):
        window = compute_time_windows(seeded_db, 2, 2, "2024-07-01T00:00:00")
        assert window.bot_adoption_date == datetime(2024, 7, 1)
        assert window.pre_months == 2
        assert window.post_months == 2

    def test_compute_time_windows_missing(self, analysis_db):
        with pytest.raises(ValueError, match="No bot_adoption_date"):
            compute_time_windows(analysis_db, 3, 3)

    def test_classify_pr_period(self):
        window = TimeWindow(
            bot_adoption_date=datetime(2024, 6, 1),
            pre_start=datetime(2024, 3, 1),
            pre_end=datetime(2024, 6, 1),
            post_start=datetime(2024, 6, 1),
            post_end=datetime(2024, 9, 1),
            pre_months=3,
            post_months=3,
        )
        assert classify_pr_period("2024-04-15T10:00:00", window) == "pre"
        assert classify_pr_period("2024-07-15T10:00:00", window) == "post"
        assert classify_pr_period("2024-01-01T10:00:00", window) is None
        assert classify_pr_period("2024-12-01T10:00:00", window) is None


# --- Contributors ---

class TestContributors:
    def test_classify_new_contributor(self, seeded_db):
        # user 1's first PR is 2024-04-01, so at that time they're new
        result = classify_contributor(seeded_db, 1, "2024-04-01T10:00:00")
        assert result == "new"

    def test_classify_established_contributor(self, seeded_db):
        # user 1 has a PR at 2024-04-01, so by 2024-05-01 they're established
        result = classify_contributor(seeded_db, 1, "2024-05-01T10:00:00")
        assert result == "established"

    def test_compute_retention_within_period(self, seeded_db):
        """New contributors with 2+ total PRs in a period should be marked as returned."""
        from setu_rp.analysis.analyzer import run_analysis

        run_id = run_analysis(seeded_db, pre_months=3, post_months=3)

        # Check that returned_in_period was set for new contributors
        rows = seeded_db.execute(
            "SELECT * FROM contributor_metrics "
            "WHERE analysis_run_id = ? AND contributor_type = 'new'",
            (run_id,),
        ).fetchall()
        for row in rows:
            # Count total PRs by this author in this period (across all types)
            total_prs = seeded_db.execute(
                "SELECT COUNT(*) as cnt FROM pr_metrics "
                "WHERE analysis_run_id = ? AND period = ? AND author_id = ?",
                (run_id, row["period"], row["user_id"]),
            ).fetchone()["cnt"]
            if total_prs >= 2:
                assert row["returned_in_period"] == 1
            else:
                assert row["returned_in_period"] == 0


# --- RQ1 Metrics ---

class TestMetricsRQ1:
    def test_time_to_merge_merged(self):
        pr = {"created_at": "2024-04-01T10:00:00", "merged_at": "2024-04-02T10:00:00"}
        assert compute_time_to_merge(pr) == 24.0

    def test_time_to_merge_not_merged(self):
        pr = {"created_at": "2024-04-01T10:00:00", "merged_at": None}
        assert compute_time_to_merge(pr) is None

    def test_time_to_first_feedback(self, seeded_db):
        # PR 101 has a review at 11:00, review comment at 11:30, issue comment at 13:00
        # PR created at 10:00, earliest non-author feedback is review at 11:00 (by user 3)
        ttff = compute_time_to_first_feedback(seeded_db, 101, "2024-04-01T10:00:00")
        assert ttff == 1.0  # 1 hour

    def test_review_iterations(self, seeded_db):
        count = compute_review_iterations(seeded_db, 101)
        assert count == 1  # One CHANGES_REQUESTED review

    def test_rejection_closed_no_merge(self):
        assert compute_rejection({"state": "closed", "merged_at": None}) == 1

    def test_rejection_merged(self):
        assert compute_rejection({"state": "closed", "merged_at": "2024-04-02T10:00:00"}) == 0

    def test_rejection_open(self):
        assert compute_rejection({"state": "open", "merged_at": None}) is None


# --- RQ2 Metrics ---

class TestMetricsRQ2:
    def test_count_comments_by_type(self, seeded_db):
        result = count_comments_by_type(seeded_db, 101)
        assert result["human_review_comments"] == 1  # user 3
        assert result["bot_review_comments"] == 1     # user 2 (Bot)
        assert result["human_issue_comments"] == 1     # user 3
        assert result["bot_issue_comments"] == 0


# --- Statistics ---

class TestStatistics:
    def test_descriptive_stats_normal(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = descriptive_stats(values)
        assert result["n"] == 5
        assert result["mean"] == 3.0
        assert result["median"] == 3.0
        assert result["min_val"] == 1.0
        assert result["max_val"] == 5.0

    def test_descriptive_stats_empty(self):
        result = descriptive_stats([])
        assert result["n"] == 0
        assert result["mean"] is None

    def test_check_normality(self):
        import numpy as np
        rng = np.random.default_rng(42)
        normal_data = rng.normal(0, 1, 100).tolist()
        is_normal, p = check_normality(normal_data)
        # Large normal sample should pass
        assert p > 0.01

    def test_cohens_d_identical(self):
        assert cohens_d([1, 2, 3], [1, 2, 3]) == 0.0

    def test_cohens_d_different(self):
        d = cohens_d([1, 2, 3], [4, 5, 6])
        assert d > 0  # post is larger

    def test_choose_and_run_test_insufficient(self):
        result = choose_and_run_test([1.0], [2.0])
        assert result["test_name"] == "insufficient_data"

    def test_choose_and_run_test_normal(self):
        import numpy as np
        rng = np.random.default_rng(42)
        pre = rng.normal(10, 2, 50).tolist()
        post = rng.normal(12, 2, 50).tolist()
        result = choose_and_run_test(pre, post)
        assert result["test_name"] in ("welch_t_test", "mann_whitney_u")
        assert result["p_value"] is not None
        assert result["pre_n"] == 50
        assert result["post_n"] == 50

    def test_proportion_test(self):
        result = run_proportion_test(30, 100, 45, 100)
        assert result["test_name"] == "proportion_z_test"
        assert result["p_value"] is not None

    def test_proportion_test_empty(self):
        result = run_proportion_test(0, 0, 10, 50)
        assert result["significant"] == 0


# --- Analyzer Integration ---

class TestAnalyzer:
    def test_run_analysis(self, seeded_db):
        from setu_rp.analysis.analyzer import run_analysis

        run_id = run_analysis(
            seeded_db,
            pre_months=3,
            post_months=3,
            sensitivity_windows=[2, 3],
        )

        assert run_id is not None

        # Check analysis_runs
        run = seeded_db.execute(
            "SELECT * FROM analysis_runs WHERE id = ?", (run_id,)
        ).fetchone()
        assert run is not None
        assert run["bot_adoption_date"] is not None

        # Check pr_metrics were computed
        pr_count = seeded_db.execute(
            "SELECT COUNT(*) as cnt FROM pr_metrics WHERE analysis_run_id = ?",
            (run_id,),
        ).fetchone()["cnt"]
        assert pr_count > 0

        # Check contributor_metrics
        cm_count = seeded_db.execute(
            "SELECT COUNT(*) as cnt FROM contributor_metrics WHERE analysis_run_id = ?",
            (run_id,),
        ).fetchone()["cnt"]
        assert cm_count > 0

        # Check period_statistics
        ps_count = seeded_db.execute(
            "SELECT COUNT(*) as cnt FROM period_statistics WHERE analysis_run_id = ?",
            (run_id,),
        ).fetchone()["cnt"]
        assert ps_count > 0

        # Check statistical_tests
        st_count = seeded_db.execute(
            "SELECT COUNT(*) as cnt FROM statistical_tests WHERE analysis_run_id = ?",
            (run_id,),
        ).fetchone()["cnt"]
        assert st_count > 0

        # Check sensitivity_results
        sr_count = seeded_db.execute(
            "SELECT COUNT(*) as cnt FROM sensitivity_results WHERE analysis_run_id = ?",
            (run_id,),
        ).fetchone()["cnt"]
        assert sr_count > 0

    def test_bot_authors_excluded(self, seeded_db):
        """Bot-authored PRs should not appear in pr_metrics."""
        # Add a bot-authored PR in the pre-period
        seeded_db.execute(
            "INSERT INTO pull_requests (id, number, state, author_id, created_at, updated_at, "
            "closed_at, merged_at, additions, deletions, changed_files, fetched_at) "
            "VALUES (105, 5, 'closed', 2, '2024-04-15T10:00:00', '2024-04-16T10:00:00', "
            "'2024-04-16T10:00:00', '2024-04-16T10:00:00', 1, 1, 1, '2024-01-01')"
        )
        seeded_db.commit()

        from setu_rp.analysis.analyzer import run_analysis

        run_id = run_analysis(seeded_db, pre_months=3, post_months=3)

        # Bot user (id=2) should have no PRs in pr_metrics
        bot_prs = seeded_db.execute(
            "SELECT COUNT(*) as cnt FROM pr_metrics WHERE analysis_run_id = ? AND author_id = 2",
            (run_id,),
        ).fetchone()["cnt"]
        assert bot_prs == 0

    def test_open_pr_rejection_is_null(self, seeded_db):
        """Open PRs should have was_rejected = NULL, not 0."""
        from setu_rp.analysis.analyzer import run_analysis

        run_id = run_analysis(seeded_db, pre_months=3, post_months=3)

        # PR 104 is open — check its was_rejected value
        row = seeded_db.execute(
            "SELECT was_rejected FROM pr_metrics WHERE pull_request_id = 104 AND analysis_run_id = ?",
            (run_id,),
        ).fetchone()
        if row:  # only if PR 104 falls in a window
            assert row["was_rejected"] is None

    def test_retention_compares_pre_vs_post(self, seeded_db):
        """Retention test should compare pre vs post rates with proportion z-test."""
        from setu_rp.analysis.analyzer import run_analysis

        run_id = run_analysis(seeded_db, pre_months=3, post_months=3)

        retention_tests = seeded_db.execute(
            "SELECT * FROM statistical_tests "
            "WHERE analysis_run_id = ? AND metric_name = 'retention_rate'",
            (run_id,),
        ).fetchall()

        assert len(retention_tests) == 1
        test = retention_tests[0]
        assert test["contributor_type"] == "new"
        assert test["test_name"] == "proportion_z_test"
        # Notes should contain both pre and post rates
        assert "Pre:" in test["notes"]
        assert "Post:" in test["notes"]

    def test_no_retention_new_vs_established(self, seeded_db):
        """retention_rate_new_vs_established test should no longer exist."""
        from setu_rp.analysis.analyzer import run_analysis

        run_id = run_analysis(seeded_db, pre_months=3, post_months=3)

        old_tests = seeded_db.execute(
            "SELECT COUNT(*) as cnt FROM statistical_tests "
            "WHERE analysis_run_id = ? AND metric_name = 'retention_rate_new_vs_established'",
            (run_id,),
        ).fetchone()["cnt"]
        assert old_tests == 0


# --- Phase A: New Metrics ---

class TestHumanFeedback:
    def test_time_to_first_human_feedback_excludes_bots(self, seeded_db):
        """Human TTFF should skip bot comments and find human feedback."""
        # PR 101: bot comment at 11:30, human review at 11:00, human review comment at 12:30
        # The earliest *human* feedback is the review at 11:00 (by user 3)
        ttff_human = compute_time_to_first_human_feedback(
            seeded_db, 101, "2024-04-01T10:00:00"
        )
        assert ttff_human == 1.0  # human review at 11:00, 1 hour after creation

    def test_time_to_first_human_feedback_no_humans(self, seeded_db):
        """If only bots have commented, human TTFF should be None."""
        # Create a PR with only a bot comment
        seeded_db.execute(
            "INSERT INTO pull_requests (id, number, state, author_id, created_at, updated_at, "
            "closed_at, merged_at, additions, deletions, changed_files, fetched_at) "
            "VALUES (106, 6, 'open', 1, '2024-07-15T10:00:00', '2024-07-15T12:00:00', "
            "NULL, NULL, 5, 2, 1, '2024-01-01')"
        )
        seeded_db.execute(
            "INSERT INTO review_comments (id, pull_request_id, review_id, author_id, body, path, "
            "created_at, updated_at, fetched_at) "
            "VALUES (2010, 106, NULL, 2, 'Bot says hi', 'main.py', "
            "'2024-07-15T10:01:00', '2024-07-15T10:01:00', '2024-01-01')"
        )
        seeded_db.commit()

        ttff_human = compute_time_to_first_human_feedback(
            seeded_db, 106, "2024-07-15T10:00:00"
        )
        assert ttff_human is None


class TestWilsonCI:
    def test_wilson_ci_basic(self):
        lo, hi = wilson_ci(50, 100)
        assert 0.39 < lo < 0.41
        assert 0.59 < hi < 0.61

    def test_wilson_ci_zero(self):
        lo, hi = wilson_ci(0, 100)
        assert lo < 0.001  # effectively zero (floating-point)
        assert hi < 0.05

    def test_wilson_ci_empty(self):
        lo, hi = wilson_ci(0, 0)
        assert lo == 0.0
        assert hi == 0.0


class TestBenjaminiHochberg:
    def test_bh_basic(self):
        # 5 tests: adjusted = raw * m / rank
        p_values = [(1, 0.001), (2, 0.04), (3, 0.03), (4, 0.20), (5, 0.50)]
        results = benjamini_hochberg(p_values)
        assert len(results) == 5
        # Results are (row_id, adjusted_p, significant)
        result_dict = {r[0]: {"adj_p": r[1], "sig": r[2]} for r in results}
        # p=0.001 adjusted = 0.001 * 5/1 = 0.005, should stay significant
        assert result_dict[1]["adj_p"] < 0.05
        assert result_dict[1]["sig"] is True
        # p=0.50 adjusted >= 0.50, should not be significant
        assert result_dict[5]["adj_p"] >= 0.05
        assert result_dict[5]["sig"] is False

    def test_bh_empty(self):
        assert benjamini_hochberg([]) == []

    def test_bh_with_nones(self):
        p_values = [(1, 0.01), (2, None), (3, 0.03)]
        results = benjamini_hochberg(p_values)
        assert len(results) == 2  # None excluded

    def test_fdr_applied_in_analysis(self, seeded_db):
        """FDR correction should populate p_value_adjusted after analysis."""
        from setu_rp.analysis.analyzer import run_analysis

        run_id = run_analysis(seeded_db, pre_months=3, post_months=3)

        # Check that at least some tests have adjusted p-values
        rows = seeded_db.execute(
            "SELECT p_value_adjusted, adjusted_significant FROM statistical_tests "
            "WHERE analysis_run_id = ? AND p_value IS NOT NULL",
            (run_id,),
        ).fetchall()
        assert len(rows) > 0
        for row in rows:
            assert row["p_value_adjusted"] is not None
            assert row["adjusted_significant"] in (0, 1)


class TestHumanReview:
    def test_time_to_first_human_review(self, seeded_db):
        """Human review time should find the earliest human review submission."""
        # PR 101 has reviews from user 3 (human): CHANGES_REQUESTED at 11:00, APPROVED at 12:00
        ttfr = compute_time_to_first_human_review(
            seeded_db, 101, "2024-04-01T10:00:00"
        )
        assert ttfr == 1.0  # CHANGES_REQUESTED at 11:00, 1 hour after creation

    def test_time_to_first_human_review_no_reviews(self, seeded_db):
        """PR with no human reviews should return None."""
        ttfr = compute_time_to_first_human_review(
            seeded_db, 102, "2024-05-01T10:00:00"
        )
        assert ttfr is None


class TestSensitivityProportions:
    def test_sensitivity_includes_rejection_rate(self, seeded_db):
        """Sensitivity analysis should include rejection_rate metric."""
        from setu_rp.analysis.analyzer import run_analysis

        run_id = run_analysis(
            seeded_db, pre_months=3, post_months=3, sensitivity_windows=[2, 3]
        )

        rej_results = seeded_db.execute(
            "SELECT COUNT(*) as cnt FROM sensitivity_results "
            "WHERE analysis_run_id = ? AND metric_name = 'rejection_rate'",
            (run_id,),
        ).fetchone()["cnt"]
        assert rej_results > 0

    def test_sensitivity_includes_retention_rate(self, seeded_db):
        """Sensitivity analysis should include retention_rate with pre and post means."""
        from setu_rp.analysis.analyzer import run_analysis

        run_id = run_analysis(
            seeded_db, pre_months=3, post_months=3, sensitivity_windows=[2, 3]
        )

        ret_results = seeded_db.execute(
            "SELECT * FROM sensitivity_results "
            "WHERE analysis_run_id = ? AND metric_name = 'retention_rate'",
            (run_id,),
        ).fetchall()
        assert len(ret_results) > 0
        # All retention sensitivity rows should be for 'new' contributors
        for row in ret_results:
            assert row["contributor_type"] == "new"
