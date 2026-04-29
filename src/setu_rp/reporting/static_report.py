"""Headless report generator for Tekton pipeline (static HTML + CSV + figures)."""

import csv
import logging
import sqlite3
from pathlib import Path

from setu_rp.reporting.charts import (
    bar_chart_rates,
    box_plot_pre_post,
    sensitivity_line_chart,
    timeline_chart,
)

logger = logging.getLogger(__name__)

METRIC_LABELS = {
    "time_to_merge_hours": ("Time to Merge", "Hours"),
    "time_to_first_human_feedback_hours": ("Time to First Human Feedback", "Hours"),
    "time_to_first_human_review_hours": ("Time to First Human Review", "Hours"),
    "time_to_first_feedback_hours": ("Time to First Feedback (all users)", "Hours"),
    "review_iterations": ("Review Iterations", "Count"),
    "total_human_comments": ("Total Human Comments", "Count"),
    "total_bot_comments": ("Total Bot Comments", "Count"),
    "human_review_comment_count": ("Human Review Comments", "Count"),
    "bot_review_comment_count": ("Bot Review Comments", "Count"),
    "avg_human_sentiment": ("Human Comment Sentiment", "Compound Score"),
}


def generate_report(
    conn: sqlite3.Connection,
    output_dir: str = "reports",
    report_format: str = "both",
):
    """Generate static report artifacts.

    Args:
        conn: Database connection with analysis tables populated.
        output_dir: Directory to write output files.
        report_format: 'html', 'csv', or 'both'.
    """
    base = Path(output_dir)
    figures_dir = base / "figures"
    tables_dir = base / "tables"
    figures_dir.mkdir(parents=True, exist_ok=True)
    tables_dir.mkdir(parents=True, exist_ok=True)

    run = conn.execute(
        "SELECT * FROM analysis_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if run is None:
        logger.error("No analysis runs found. Run 'analyze' first.")
        return
    run_id = run["id"]
    logger.info("Generating report for analysis run %d", run_id)

    # Generate figures
    _generate_figures(conn, run_id, figures_dir)

    # Export CSVs
    if report_format in ("csv", "both"):
        _export_csvs(conn, run_id, tables_dir)

    # Generate HTML
    if report_format in ("html", "both"):
        _generate_html(conn, run_id, base, figures_dir)

    logger.info("Report written to %s", base)


def _generate_figures(conn: sqlite3.Connection, run_id: int, figures_dir: Path):
    """Generate all chart figures."""
    # Timeline
    timeline_chart(conn, run_id, output_path=str(figures_dir / "timeline.png"))

    # Box plots for continuous metrics
    for metric, (title, ylabel) in METRIC_LABELS.items():
        box_plot_pre_post(
            conn,
            run_id,
            metric,
            f"{title} (Pre vs Post)",
            ylabel,
            output_path=str(figures_dir / f"{metric}_box.png"),
        )

    # Retention bar chart
    retention_data = _get_retention_rates(conn, run_id)
    if retention_data:
        labels, pre_rates, post_rates = retention_data
        bar_chart_rates(
            labels,
            pre_rates,
            post_rates,
            "Contributor Retention Rate",
            "Rate",
            output_path=str(figures_dir / "retention_rates.png"),
        )

    # Rejection bar chart
    rejection_data = _get_rejection_rates(conn, run_id)
    if rejection_data:
        labels, pre_rates, post_rates = rejection_data
        bar_chart_rates(
            labels,
            pre_rates,
            post_rates,
            "PR Rejection Rate",
            "Rate",
            output_path=str(figures_dir / "rejection_rates.png"),
        )

    # Sensitivity charts
    for metric in METRIC_LABELS:
        sensitivity_line_chart(
            conn,
            run_id,
            metric,
            output_path=str(figures_dir / f"{metric}_sensitivity.png"),
        )

    logger.info("Figures written to %s", figures_dir)


def _export_csvs(conn: sqlite3.Connection, run_id: int, tables_dir: Path):
    """Export analysis tables as CSV files."""
    tables = [
        ("pr_metrics", "SELECT * FROM pr_metrics WHERE analysis_run_id = ?"),
        ("contributor_metrics", "SELECT * FROM contributor_metrics WHERE analysis_run_id = ?"),
        ("period_statistics", "SELECT * FROM period_statistics WHERE analysis_run_id = ?"),
        ("statistical_tests", "SELECT * FROM statistical_tests WHERE analysis_run_id = ?"),
        ("sensitivity_results", "SELECT * FROM sensitivity_results WHERE analysis_run_id = ?"),
        ("governance_changes", "SELECT * FROM governance_changes WHERE analysis_run_id = ?"),
    ]
    for name, query in tables:
        rows = conn.execute(query, (run_id,)).fetchall()
        if not rows:
            continue
        path = tables_dir / f"{name}.csv"
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(rows[0].keys())
            writer.writerows(rows)
    logger.info("CSVs written to %s", tables_dir)


def _generate_html(
    conn: sqlite3.Connection, run_id: int, base: Path, figures_dir: Path
):
    """Generate an HTML report."""
    run = conn.execute("SELECT * FROM analysis_runs WHERE id = ?", (run_id,)).fetchone()

    # Statistical test results
    tests = conn.execute(
        "SELECT * FROM statistical_tests WHERE analysis_run_id = ? ORDER BY metric_name",
        (run_id,),
    ).fetchall()

    # Period statistics
    stats = conn.execute(
        "SELECT * FROM period_statistics WHERE analysis_run_id = ? "
        "ORDER BY metric_name, period, contributor_type",
        (run_id,),
    ).fetchall()

    html = _build_html(conn, run_id, run, tests, stats, figures_dir)
    (base / "report.html").write_text(html)
    logger.info("HTML report written to %s", base / "report.html")


def _build_html(conn, run_id, run, tests, stats, figures_dir: Path) -> str:
    """Build the HTML report string."""
    fig = figures_dir.name

    # --- helpers ---
    def _fig(name: str) -> str:
        path = figures_dir / name
        if not path.exists():
            return ""
        return (
            f'<div class="figure"><img src="{fig}/{name}" '
            f'alt="{name}"></div>\n'
        )

    def _stats_table(metric_names: list[str]) -> str:
        rows = ""
        for s in stats:
            if s["metric_name"] not in metric_names:
                continue
            label = METRIC_LABELS.get(s["metric_name"], (s["metric_name"], ""))[0]
            mean = f"{s['mean']:.2f}" if s["mean"] is not None else "N/A"
            median = f"{s['median']:.2f}" if s["median"] is not None else "N/A"
            std = f"{s['std_dev']:.2f}" if s["std_dev"] is not None else "N/A"
            q1 = f"{s['q1']:.2f}" if s["q1"] is not None else "N/A"
            q3 = f"{s['q3']:.2f}" if s["q3"] is not None else "N/A"
            rows += (
                f"<tr><td>{label}</td><td>{s['period']}</td>"
                f"<td>{s['contributor_type']}</td><td>{s['n']}</td>"
                f"<td>{mean}</td><td>{median}</td><td>{std}</td>"
                f"<td>{q1}</td><td>{q3}</td></tr>\n"
            )
        if not rows:
            return "<p><em>No data available for these metrics.</em></p>"
        return (
            '<table>\n<tr><th>Metric</th><th>Period</th><th>Type</th>'
            "<th>N</th><th>Mean</th><th>Median</th><th>Std Dev</th>"
            "<th>Q1</th><th>Q3</th></tr>\n"
            f"{rows}</table>\n"
        )

    def _tests_table(metric_names: list[str]) -> str:
        rows = ""
        for t in tests:
            if t["metric_name"] not in metric_names:
                continue
            label = METRIC_LABELS.get(t["metric_name"], (t["metric_name"], ""))[0]
            adj_sig = t["adjusted_significant"]
            if adj_sig is not None:
                sig = "Yes" if adj_sig else "No"
                sig_class = "sig-yes" if adj_sig else "sig-no"
            else:
                sig = "Yes" if t["significant"] else "No"
                sig_class = "sig-yes" if t["significant"] else "sig-no"
            p_val = f"{t['p_value']:.4f}" if t["p_value"] is not None else "N/A"
            adj_p = t["p_value_adjusted"]
            p_display = p_val
            if adj_p is not None:
                p_display += f" (adj: {adj_p:.4f})"
            effect = f"{t['effect_size']:.4f}" if t["effect_size"] is not None else "N/A"
            ci = ""
            if t["ci_lower"] is not None and t["ci_upper"] is not None:
                ci = f"[{t['ci_lower']:.3f}, {t['ci_upper']:.3f}]"
            rows += (
                f"<tr><td>{label}</td><td>{t['contributor_type']}</td>"
                f"<td>{t['test_name']}</td><td>{p_display}</td>"
                f"<td>{effect} ({t['effect_size_type'] or 'N/A'})</td>"
                f"<td>{ci or 'N/A'}</td>"
                f'<td class="{sig_class}">{sig}</td></tr>\n'
            )
        if not rows:
            return "<p><em>No test results available for these metrics.</em></p>"
        return (
            "<table>\n<tr><th>Metric</th><th>Type</th><th>Test</th>"
            "<th>p-value</th><th>Effect Size</th><th>95% CI</th>"
            "<th>Sig.</th></tr>\n"
            f"{rows}</table>\n"
        )

    prs_pre = run["total_prs_pre"] or 0
    prs_post = run["total_prs_post"] or 0
    contrib_pre = run["total_contributors_pre"] or 0
    contrib_post = run["total_contributors_post"] or 0

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>LLM Code-Review Bot Impact Analysis</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       max-width: 1200px; margin: 0 auto; padding: 20px; color: #333; line-height: 1.6; }}
h1 {{ border-bottom: 3px solid #4C72B0; padding-bottom: 10px; }}
h2 {{ color: #4C72B0; margin-top: 40px; border-bottom: 1px solid #ddd; padding-bottom: 5px; }}
h3 {{ color: #555; }}
table {{ border-collapse: collapse; width: 100%; margin: 15px 0; font-size: 0.9em; }}
th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
th {{ background: #4C72B0; color: white; }}
tr:nth-child(even) {{ background: #f9f9f9; }}
.figure {{ margin: 20px 0; text-align: center; }}
.figure img {{ max-width: 100%; height: auto; border: 1px solid #ddd; border-radius: 4px; }}
.summary {{ background: #f0f4f8; padding: 20px; border-radius: 8px; margin: 20px 0;
            border-left: 4px solid #4C72B0; }}
.note {{ background: #fafafa; padding: 15px; border-radius: 5px; margin: 15px 0;
         border-left: 3px solid #888; font-size: 0.95em; color: #555; }}
.sig-yes {{ color: #2e7d32; font-weight: bold; }}
.sig-no {{ color: #888; }}
.metric-section {{ margin-bottom: 30px; padding-bottom: 20px; border-bottom: 1px dashed #ddd; }}
footer {{ margin-top: 40px; padding-top: 15px; border-top: 1px solid #ddd; color: #888;
          font-size: 0.85em; }}
</style>
</head>
<body>
<h1>LLM Code-Review Bot Impact Analysis</h1>

<!-- ===== SUMMARY ===== -->
<div class="summary">
<h2 style="margin-top:0; border:none;">Analysis Summary</h2>
<p>This report presents a quasi-experimental interrupted time-series analysis
comparing pull request activity <strong>before</strong> and <strong>after</strong>
the adoption of an LLM-based code-review bot in the
<code>openshift/hypershift</code> repository.</p>
<table style="width:auto; margin:10px 0;">
<tr><th></th><th>Start</th><th>End</th><th>Months</th><th>PRs</th><th>Contributors</th></tr>
<tr><td><strong>Pre-period</strong></td>
    <td>{run['pre_start'][:10]}</td><td>{run['pre_end'][:10]}</td>
    <td>{run['pre_window_months']}</td><td>{prs_pre}</td><td>{contrib_pre}</td></tr>
<tr><td><strong>Post-period</strong></td>
    <td>{run['post_start'][:10]}</td><td>{run['post_end'][:10]}</td>
    <td>{run['post_window_months']}</td><td>{prs_post}</td><td>{contrib_post}</td></tr>
</table>
<p><strong>Bot adoption date:</strong> {run['bot_adoption_date'][:10]}</p>
</div>

<!-- ===== TIMELINE ===== -->
<h2>1. Dataset Overview</h2>
<div class="note">
<p>The timeline below shows monthly PR volume across the analysis window.
Blue bars represent the pre-adoption period and orange bars represent the
post-adoption period. The red dashed line marks the bot adoption date.
Differences in bar height reflect natural variation in project activity and
should be considered when interpreting statistical results.</p>
</div>
{_fig("timeline.png")}

<!-- ===== RQ1: RETENTION ===== -->
<h2>2. RQ1: Contributor Retention</h2>
<div class="note">
<p><strong>Research question:</strong> How does the introduction of an LLM-based
code-review bot affect contributor retention rates?</p>
<p>Retention is defined as the proportion of <em>new</em> contributors who
submitted 2 or more PRs within the same period. Pre-period and post-period
retention rates are independently computed, then compared using a
two-proportion z-test with the odds ratio as effect size. This within-period
definition avoids conflating retention with cross-period activity.</p>
</div>
{_fig("retention_rates.png")}

<h3>Statistical Tests &mdash; Retention</h3>
{_tests_table(["retention_rate"])}

<!-- ===== RQ1: EFFICIENCY ===== -->
<h2>3. RQ1: Development Efficiency</h2>
<div class="note">
<p><strong>Research question:</strong> Does bot adoption change how quickly PRs are
reviewed and merged?</p>
<p>Four efficiency metrics are examined:</p>
<ul>
<li><strong>Time to merge</strong> &mdash; hours from PR creation to merge.
    Captures end-to-end throughput. Only includes merged PRs.</li>
<li><strong>Time to first human feedback</strong> &mdash; hours from PR creation to
    the earliest review, review comment, or issue comment by a <em>human</em>
    reviewer (excluding bots). The primary responsiveness metric.</li>
<li><strong>Time to first human review</strong> &mdash; hours from PR creation to
    the first formal review submission (APPROVED, CHANGES_REQUESTED, COMMENTED)
    by a human reviewer.</li>
<li><strong>Time to first feedback (all users)</strong> &mdash; same as above but
    including bot accounts. Retained for completeness; dominated by near-instant
    bot responses in the post-period.</li>
<li><strong>Review iterations</strong> &mdash; count of
    <code>CHANGES_REQUESTED</code> reviews on each PR. A proxy for revision
    cycles; GitHub's API does not expose explicit iteration boundaries.</li>
<li><strong>Rejection rate</strong> &mdash; proportion of PRs closed without
    merging. Tested via a two-proportion z-test.</li>
</ul>
<p>For continuous metrics, normality is checked via the Shapiro-Wilk test.
If both pre and post samples are normal, Welch's t-test is used with
Cohen's d as effect size; otherwise, the Mann-Whitney U test is used with
rank-biserial correlation.</p>
</div>

<h3>Time to Merge</h3>
<div class="metric-section">
{_fig("time_to_merge_hours_box.png")}
<div class="note">
<p>Box plots compare the distribution of merge times before and after bot
adoption. The box spans the interquartile range (Q1&ndash;Q3), the line
inside marks the median, and whiskers extend to 1.5&times;IQR. Outliers
appear as dots beyond the whiskers. A shift in median or compression of
the box suggests a change in typical merge speed.</p>
</div>
{_stats_table(["time_to_merge_hours"])}
{_tests_table(["time_to_merge_hours"])}
</div>

<h3>Time to First Human Feedback</h3>
<div class="metric-section">
{_fig("time_to_first_human_feedback_hours_box.png")}
<div class="note">
<p>Time to first <strong>human</strong> feedback measures initial reviewer
responsiveness, excluding bot accounts (e.g., CodeRabbit, CI bots). This is the
primary TTFF metric &mdash; it isolates genuine human engagement from automated
responses that arrive within seconds of PR creation.</p>
</div>
{_stats_table(["time_to_first_human_feedback_hours"])}
{_tests_table(["time_to_first_human_feedback_hours"])}
</div>

<h3>Time to First Human Review</h3>
<div class="metric-section">
{_fig("time_to_first_human_review_hours_box.png")}
<div class="note">
<p>Time to first <strong>human review</strong> measures when a human reviewer
formally submitted a review (APPROVED, CHANGES_REQUESTED, or COMMENTED). This
separates &ldquo;someone formally evaluated the code&rdquo; from casual comments.</p>
</div>
{_stats_table(["time_to_first_human_review_hours"])}
{_tests_table(["time_to_first_human_review_hours"])}
</div>

<h3>Time to First Feedback (all users)</h3>
<div class="metric-section">
{_fig("time_to_first_feedback_hours_box.png")}
<div class="note">
<p>Time to first feedback from <em>any</em> non-author user, including bots.
Retained for completeness; in the post-period this metric is dominated by
near-instant bot auto-replies and should not be used to assess human reviewer
responsiveness.</p>
</div>
{_stats_table(["time_to_first_feedback_hours"])}
{_tests_table(["time_to_first_feedback_hours"])}
</div>

<h3>Review Iterations</h3>
<div class="metric-section">
{_fig("review_iterations_box.png")}
<div class="note">
<p>Review iterations are approximated by counting <code>CHANGES_REQUESTED</code>
reviews. This is a lower-bound proxy &mdash; some iteration cycles may occur
via comments without a formal review state change. A decrease in iterations
post-adoption may indicate the bot catches issues early, reducing back-and-forth.</p>
</div>
{_stats_table(["review_iterations"])}
{_tests_table(["review_iterations"])}
</div>

<h3>PR Rejection Rate</h3>
<div class="metric-section">
{_fig("rejection_rates.png")}
<div class="note">
<p>Rejection rate is the proportion of PRs that were closed without being merged.
This can reflect code quality, alignment with project standards, or contributor
experience. Compared using a two-proportion z-test with the odds ratio as
effect size and a 95% confidence interval.</p>
</div>
{_tests_table(["rejection_rate"])}
</div>

<!-- ===== RQ2: BEHAVIOUR ===== -->
<h2>4. RQ2: Maintainer Review Behaviour</h2>
<div class="note">
<p><strong>Research question:</strong> How does bot adoption change maintainer
review behaviour?</p>
<p>Comment counts are computed per PR by joining with the users table and
grouping by user type (User vs Bot). This yields four counts per PR:</p>
<ul>
<li><strong>Human review comments</strong> &mdash; inline code review comments
    from human reviewers.</li>
<li><strong>Bot review comments</strong> &mdash; inline code review comments
    from bot accounts (e.g., CodeRabbit).</li>
<li><strong>Total human comments</strong> &mdash; all human comments (review +
    issue comments combined).</li>
<li><strong>Total bot comments</strong> &mdash; all bot comments combined.</li>
</ul>
<p>A decrease in human comments alongside an increase in bot comments may suggest
the bot is absorbing routine review work. Stable human comments with additional
bot comments would suggest the bot supplements rather than replaces human review.</p>
</div>

<h3>Human Comments</h3>
<div class="metric-section">
{_fig("total_human_comments_box.png")}
{_fig("human_review_comment_count_box.png")}
{_stats_table(["total_human_comments", "human_review_comment_count"])}
{_tests_table(["total_human_comments", "human_review_comment_count"])}
</div>

<h3>Bot Comments</h3>
<div class="metric-section">
{_fig("total_bot_comments_box.png")}
{_fig("bot_review_comment_count_box.png")}
<div class="note">
<p>If bot comment counts are zero across both periods, review comments and/or
issue comments may not have been collected yet. Run <code>make collect</code>
to fetch the remaining data, then <code>make analyze</code> and
<code>make report</code> to regenerate.</p>
</div>
{_stats_table(["total_bot_comments", "bot_review_comment_count"])}
{_tests_table(["total_bot_comments", "bot_review_comment_count"])}
</div>

<!-- ===== RQ2: SENTIMENT ===== -->
<h2>5. RQ2: Comment Sentiment</h2>
<div class="note">
<p><strong>Research question:</strong> Does bot adoption change the tone of human
review comments?</p>
<p>Sentiment is measured using the VADER (Valence Aware Dictionary and sEntiment
Reasoner) lexicon, which produces a compound score from &minus;1 (most negative)
to +1 (most positive). Comment text is preprocessed to remove code blocks,
inline code, URLs, @mentions, and HTML tags before scoring. Code-review-specific
terms (LGTM, nit, PTAL) are added to the lexicon.</p>
<p>The <strong>avg_human_sentiment</strong> metric is the mean compound score of
all human comments on each PR. A shift in sentiment post-adoption may indicate
changes in reviewer attitude or communication style.</p>
</div>

<h3>Human Comment Sentiment</h3>
<div class="metric-section">
{_fig("avg_human_sentiment_box.png")}
{_stats_table(["avg_human_sentiment"])}
{_tests_table(["avg_human_sentiment"])}
</div>

<!-- ===== RQ2: GOVERNANCE ===== -->
<h2>6. RQ2: Governance Document Changes</h2>
<div class="note">
<p><strong>Research question:</strong> Do maintainers change contribution guidelines
or review policies after bot adoption?</p>
<p>This section tracks changes to governance files (CONTRIBUTING.md, PR templates,
OWNERS, REVIEWERS) across the pre and post periods. Changes are analysed for
governance-related keywords (required, approval, reviewer, bot, automated,
policy, checklist, merge, CI, test) to detect policy adjustments.</p>
</div>
{_governance_table(conn, run_id)}

<!-- ===== SENSITIVITY ===== -->
<h2>7. Sensitivity Analysis</h2>
<div class="note">
<p>Sensitivity analysis tests whether the findings are robust to the choice of
time window size. Each metric is re-analysed using window sizes of 1, 2, 3, 4,
and 6 months around the bot adoption date. The left chart shows how the effect
size varies with window size, and the right chart shows the corresponding
p-value. The red dashed line at p&nbsp;=&nbsp;0.05 marks the conventional
significance threshold.</p>
<p>Consistent effect sizes and significance across window sizes strengthen
confidence in the findings. If results are significant only at specific window
sizes, this may indicate sensitivity to sample composition or confounding
temporal trends, and should be discussed as a limitation.</p>
</div>

<h3>Time to Merge</h3>
{_fig("time_to_merge_hours_sensitivity.png")}

<h3>Time to First Human Feedback</h3>
{_fig("time_to_first_human_feedback_hours_sensitivity.png")}

<h3>Time to First Human Review</h3>
{_fig("time_to_first_human_review_hours_sensitivity.png")}

<h3>Time to First Feedback (all users)</h3>
{_fig("time_to_first_feedback_hours_sensitivity.png")}

<h3>Review Iterations</h3>
{_fig("review_iterations_sensitivity.png")}

<h3>Human Comments</h3>
{_fig("total_human_comments_sensitivity.png")}
{_fig("human_review_comment_count_sensitivity.png")}

<h3>Bot Comments</h3>
{_fig("total_bot_comments_sensitivity.png")}
{_fig("bot_review_comment_count_sensitivity.png")}

<h3>Human Comment Sentiment</h3>
{_fig("avg_human_sentiment_sensitivity.png")}

<!-- ===== METHODOLOGY ===== -->
<h2>8. Methodology Notes</h2>
<div class="note">
<p><strong>Study design:</strong> Quasi-experimental interrupted time-series.
The bot adoption date divides the timeline into pre and post periods of equal
length. All PRs created within each window are included.</p>

<p><strong>Contributor classification:</strong> A contributor is classified as
<em>new</em> if they have no PRs in the repository prior to the current PR's
creation date (within the collected data). Otherwise they are <em>established</em>.
This classification is relative to the collected data; if collection does not
cover the full repository history, some "new" contributors may actually have
older uncollected PRs.</p>

<p><strong>Statistical tests:</strong> Each sample pair is checked for normality
using the Shapiro-Wilk test (&alpha;&nbsp;=&nbsp;0.05). If both samples pass,
Welch's t-test is used with Cohen's d as effect size and bootstrap 95% confidence
intervals (1000 iterations). If either sample is non-normal, the Mann-Whitney U
test is used with rank-biserial correlation as effect size. Proportion comparisons
(rejection) use a two-proportion z-test with odds ratio and Wald 95%
confidence interval. Retention rates compare within-period rates for new
contributors using a two-proportion z-test, with individual Wilson confidence
intervals reported for each period.</p>

<p><strong>Multiple comparison correction:</strong> All p-values are adjusted
using the Benjamini-Hochberg false discovery rate (FDR) procedure. The
&ldquo;Sig.&rdquo; column in test results tables reflects the FDR-adjusted
significance at &alpha;&nbsp;=&nbsp;0.05. Both raw and adjusted p-values are
reported for transparency.</p>

<p><strong>Bot-authored PR exclusion:</strong> PRs authored by bot accounts
(e.g., dependabot, red-hat-konflux, coderabbitai) are excluded from all metrics,
as they have fundamentally different review patterns (auto-merged, no human
review) and would skew a study focused on human contributor behaviour.</p>

<p><strong>Review iterations heuristic:</strong> GitHub's API does not expose
explicit review iteration boundaries. The count of
<code>CHANGES_REQUESTED</code> reviews is used as a lower-bound proxy.</p>

<p><strong>Effect size interpretation (Cohen's d):</strong> |d|&nbsp;&lt;&nbsp;0.2
is negligible, 0.2&ndash;0.5 is small, 0.5&ndash;0.8 is medium,
&gt;&nbsp;0.8 is large.</p>
</div>

<footer>
<p>Generated by <strong>setu-rp</strong> analysis framework &mdash;
MSc dissertation research studying LLM code-review bot impact on contributor
retention in openshift/hypershift.</p>
</footer>
</body>
</html>"""


def _governance_table(conn: sqlite3.Connection, run_id: int) -> str:
    """Build HTML table for governance document changes."""
    try:
        changes = conn.execute(
            "SELECT * FROM governance_changes WHERE analysis_run_id = ? ORDER BY change_date",
            (run_id,),
        ).fetchall()
    except Exception:
        return "<p><em>Governance analysis tables not available.</em></p>"

    if not changes:
        return (
            "<p><em>No governance document changes found. Run <code>make collect</code> "
            "to fetch governance document history.</em></p>"
        )

    pre_count = sum(1 for c in changes if c["period"] == "pre")
    post_count = sum(1 for c in changes if c["period"] == "post")
    bot_count = sum(1 for c in changes if c["bot_related"])

    # Category breakdown
    categories: dict[str, dict] = {}
    for c in changes:
        cat = c["category"] or "other"
        if cat not in categories:
            categories[cat] = {"pre": 0, "post": 0, "bot": 0, "total": 0}
        categories[cat]["total"] += 1
        if c["period"] == "pre":
            categories[cat]["pre"] += 1
        elif c["period"] == "post":
            categories[cat]["post"] += 1
        if c["bot_related"]:
            categories[cat]["bot"] += 1

    cat_rows = ""
    for cat in sorted(categories):
        d = categories[cat]
        cat_rows += (
            f"<tr><td>{cat}</td><td>{d['pre']}</td><td>{d['post']}</td>"
            f"<td>{d['total']}</td><td>{d['bot']}</td></tr>\n"
        )

    # Detail table
    detail_rows = ""
    for c in changes:
        kw_add = c["keywords_added"] or ""
        period = c["period"] or "outside"
        cat = c["category"] or ""
        bot_flag = "Yes" if c["bot_related"] else ""
        detail_rows += (
            f"<tr><td>{c['change_date'][:10]}</td><td>{c['file_path']}</td>"
            f"<td>{period}</td><td>{cat}</td><td>{bot_flag}</td>"
            f"<td>+{c['lines_added']} -{c['lines_removed']}</td>"
            f"<td>{kw_add}</td></tr>\n"
        )

    return (
        f"<p>Total governance changes: <strong>{len(changes)}</strong> "
        f"(pre: {pre_count}, post: {post_count}, bot-related: {bot_count})</p>\n"
        "<h3>Changes by Category</h3>\n"
        "<table>\n<tr><th>Category</th><th>Pre</th><th>Post</th>"
        "<th>Total</th><th>Bot-related</th></tr>\n"
        f"{cat_rows}</table>\n"
        "<h3>Change Details</h3>\n"
        "<table>\n<tr><th>Date</th><th>File</th><th>Period</th><th>Category</th>"
        "<th>Bot</th><th>Lines</th><th>Keywords Added</th></tr>\n"
        f"{detail_rows}</table>\n"
    )


def _get_retention_rates(conn: sqlite3.Connection, run_id: int):
    """Get within-period retention rates for new contributors bar chart."""
    labels = ["New Contributors"]
    pre_rates = []
    post_rates = []

    for period, rate_list in [("pre", pre_rates), ("post", post_rates)]:
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM contributor_metrics "
            "WHERE analysis_run_id = ? AND period = ? AND contributor_type = 'new'",
            (run_id, period),
        ).fetchone()["cnt"]
        returned = conn.execute(
            "SELECT COUNT(*) as cnt FROM contributor_metrics "
            "WHERE analysis_run_id = ? AND period = ? AND contributor_type = 'new' "
            "AND returned_in_period = 1",
            (run_id, period),
        ).fetchone()["cnt"]
        rate_list.append(returned / total if total > 0 else 0)

    return (labels, pre_rates, post_rates) if any(pre_rates) or any(post_rates) else None


def _get_rejection_rates(conn: sqlite3.Connection, run_id: int):
    """Get rejection rates for bar chart."""
    labels = []
    pre_rates = []
    post_rates = []

    for ctype in ["all", "new", "established"]:
        for period, rate_list in [("pre", pre_rates), ("post", post_rates)]:
            where = "analysis_run_id = ? AND period = ?"
            params: list = [run_id, period]
            if ctype != "all":
                where += " AND contributor_type = ?"
                params.append(ctype)

            total = conn.execute(
                f"SELECT COUNT(*) as cnt FROM pr_metrics WHERE {where}", params
            ).fetchone()["cnt"]
            rejected = conn.execute(
                f"SELECT COUNT(*) as cnt FROM pr_metrics WHERE {where} AND was_rejected = 1",
                params,
            ).fetchone()["cnt"]
            rate_list.append(rejected / total if total > 0 else 0)

        if ctype not in [label.lower() for label in labels]:
            labels.append(ctype.title())

    return (labels, pre_rates, post_rates) if labels else None
