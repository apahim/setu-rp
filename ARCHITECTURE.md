# Architecture Document

## Project Overview

This project implements a data analysis framework for an MSc dissertation studying the impact of LLM-based code-review bots on contributor retention and maintainer behaviour in GitHub repositories. The default target is **openshift/hypershift**, but the framework can be pointed at any public repository by editing `config.yaml`.

### Research Questions

1. How does the introduction of an LLM-based code-review bot affect contributor retention rates?
2. How does bot adoption change maintainer review behaviour (response times, review depth)?
3. Are there differential effects on new vs established contributors?

## System Components

The system follows a 5-layer architecture:

```
┌─────────────────────────────────────────────┐
│             5. Reporting Layer              │
│    (Static reports, interactive dashboard)   │
├─────────────────────────────────────────────┤
│             4. Analysis Layer               │
│  (Metrics, statistics, sentiment, governance)│
├─────────────────────────────────────────────┤
│           3. Data Collection Layer          │
│    (GitHub REST API client, collectors)      │
├─────────────────────────────────────────────┤
│             2. Storage Layer                │
│           (SQLite database)                  │
├─────────────────────────────────────────────┤
│           1. Infrastructure Layer           │
│     (Tekton pipeline, Makefile, Docker)      │
└─────────────────────────────────────────────┘
```

## Project Directory Structure

```
setu-rp/
├── ARCHITECTURE.md
├── README.md
├── Makefile
├── Dockerfile
├── config.yaml
├── pyproject.toml
├── requirements.txt
├── src/setu_rp/
│   ├── __init__.py
│   ├── cli.py                    # argparse entry points: init-db, collect, analyze, report, dashboard
│   ├── config.py                 # YAML + env var + CLI config loader
│   ├── db/
│   │   ├── __init__.py
│   │   ├── connection.py         # SQLite context manager, pragmas (WAL, FK)
│   │   ├── schema.py             # Collection table DDL (incl. governance_documents)
│   │   └── models.py             # Dataclasses for DB rows
│   ├── collection/
│   │   ├── __init__.py
│   │   ├── client.py             # GitHub REST client: auth, rate limit, pagination, retry
│   │   ├── collector.py          # Orchestrator: incremental sync, resumability, progress
│   │   ├── pull_requests.py      # PR collection logic
│   │   ├── reviews.py            # Reviews + review comments
│   │   ├── comments.py           # Issue comments
│   │   ├── labels.py             # Labels
│   │   ├── users.py              # Contributors/users
│   │   └── governance.py         # Governance document version history collection
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── analyzer.py           # Orchestrator: run_analysis() entry point
│   │   ├── time_windows.py       # Time window computation, PR period classification
│   │   ├── contributors.py       # Contributor classification (new vs established), retention
│   │   ├── metrics_rq1.py        # RQ1: retention, TTM, TTFF, TTFHF, TTFHR, iterations, rejection
│   │   ├── metrics_rq2.py        # RQ2: comment frequencies, human/bot ratios
│   │   ├── bot_detection.py      # Bot user identification (type + login patterns, SQL helpers)
│   │   ├── sentiment.py          # VADER sentiment analysis with code-review-specific lexicon
│   │   ├── governance.py         # Governance document diff analysis, keyword detection
│   │   ├── statistics.py         # Statistical tests: t-test, Mann-Whitney, proportion, effect sizes, FDR
│   │   └── schema.py             # Analysis table DDL + column migrations
│   └── reporting/
│       ├── __init__.py
│       ├── dashboard.py           # Streamlit app entry point, sidebar controls
│       ├── live_analysis.py       # Cached live recomputation engine for dynamic windows
│       ├── pages/
│       │   ├── __init__.py
│       │   ├── overview.py        # Dataset summary, timeline, data quality
│       │   ├── rq1_retention.py   # Contributor retention analysis
│       │   ├── rq1_efficiency.py  # Time-to-merge, rejection, iterations, TTFF
│       │   ├── rq2_behavior.py    # Comment patterns, human/bot ratios
│       │   ├── sensitivity.py     # Sensitivity analysis across window sizes
│       │   └── data_export.py     # CSV, APA-style reporting strings
│       ├── charts.py              # Reusable chart functions (matplotlib)
│       └── static_report.py       # Headless report generator (for Tekton)
├── scripts/
│   ├── collect.sh
│   ├── analyze.sh
│   └── report.sh
├── tekton/
│   ├── pipeline.yaml
│   ├── tasks/
│   │   ├── collect.yaml
│   │   ├── analyze.yaml
│   │   └── report.yaml
│   └── pipelinerun.yaml
├── tests/
│   ├── conftest.py
│   ├── test_analysis.py           # Analysis layer tests
│   ├── test_client.py             # HTTP client tests
│   ├── test_collector.py          # Collection orchestrator tests
│   ├── test_governance.py         # Governance collection + analysis tests
│   ├── test_schema.py             # Database schema tests
│   ├── test_sentiment.py          # Sentiment preprocessing + scoring tests
│   └── fixtures/                  # Test fixture data
└── data/                          # .gitignored runtime data
    └── hypershift.db
```

## Data Flow

```
collect:    GitHub API ──→ raw tables (pull_requests, reviews, comments, users,
                           labels, governance_documents)
                          ──→ repositories.bot_adoption_date

analyze:    raw tables  ──→ pr_metrics, contributor_metrics,
                            comment_sentiments,
                            period_statistics, statistical_tests,
                            sensitivity_results, governance_changes

report:     analysis tables ──→ reports/
                                ├── report.html
                                ├── figures/*.png
                                └── tables/*.csv

dashboard:  analysis tables ──→ Streamlit (stored results, default)
            pr_metrics + pull_requests ──→ live_analysis ──→ Streamlit (dynamic windows)
```

SQLite is the sole interface between steps. No intermediate files.

## Data Model

All data is stored in a single SQLite database. Timestamps are stored as ISO 8601 TEXT. Every collection table includes a `fetched_at` column for audit trail.

### Collection Tables

#### `sync_metadata`
Tracks the last fetch timestamp and page per entity type, enabling incremental collection and resumability.

| Column | Type | Description |
|--------|------|-------------|
| entity_type | TEXT PK | Entity being synced (e.g., "pull_requests") |
| last_sync | TEXT | ISO 8601 timestamp of last successful sync |
| last_page | INTEGER | Last completed page (for resumability) |
| total_items | INTEGER | Total items collected |
| updated_at | TEXT | When this record was last updated |

#### `repositories`
Stores repository metadata and the discovered bot adoption date.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | GitHub repo ID |
| owner | TEXT | Repository owner |
| name | TEXT | Repository name |
| full_name | TEXT | owner/name |
| bot_adoption_date | TEXT | Discovered date of first bot review activity |
| fetched_at | TEXT | When this record was fetched |

#### `users`
GitHub users encountered during collection.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | GitHub user ID |
| login | TEXT UNIQUE | GitHub username |
| type | TEXT | "User" or "Bot" |
| name | TEXT | Display name |
| fetched_at | TEXT | When this record was fetched |

**Indexes:** `idx_users_login`, `idx_users_type`

#### `pull_requests`
All pull requests from the repository.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | GitHub PR ID |
| number | INTEGER UNIQUE | PR number |
| title | TEXT | PR title |
| state | TEXT | open, closed, merged |
| author_id | INTEGER FK | References users(id) |
| created_at | TEXT | When PR was created |
| updated_at | TEXT | When PR was last updated |
| closed_at | TEXT | When PR was closed |
| merged_at | TEXT | When PR was merged |
| merge_commit_sha | TEXT | Merge commit SHA |
| additions | INTEGER | Lines added |
| deletions | INTEGER | Lines deleted |
| changed_files | INTEGER | Number of files changed |
| body | TEXT | PR description |
| fetched_at | TEXT | When this record was fetched |

**Indexes:** `idx_pr_created_at`, `idx_pr_merged_at`, `idx_pr_author_id`, `idx_pr_updated_at`, `idx_pr_state`

#### `labels`
Label definitions from the repository.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | GitHub label ID |
| name | TEXT UNIQUE | Label name |
| description | TEXT | Label description |
| fetched_at | TEXT | When this record was fetched |

**Index:** `idx_labels_name`

#### `pull_request_labels`
Junction table linking PRs to labels.

| Column | Type | Description |
|--------|------|-------------|
| pull_request_id | INTEGER FK | References pull_requests(id) |
| label_id | INTEGER FK | References labels(id) |

**Primary key:** composite (pull_request_id, label_id)

#### `reviews`
Code reviews submitted on pull requests.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | GitHub review ID |
| pull_request_id | INTEGER FK | References pull_requests(id) |
| reviewer_id | INTEGER FK | References users(id) |
| state | TEXT | APPROVED, CHANGES_REQUESTED, COMMENTED, etc. |
| body | TEXT | Review body |
| submitted_at | TEXT | When review was submitted |
| fetched_at | TEXT | When this record was fetched |

**Indexes:** `idx_reviews_pr_id`, `idx_reviews_reviewer_id`, `idx_reviews_submitted_at`, `idx_reviews_state`

#### `review_comments`
Line-level comments within code reviews.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | GitHub comment ID |
| pull_request_id | INTEGER FK | References pull_requests(id) |
| review_id | INTEGER | Associated review ID |
| author_id | INTEGER FK | References users(id) |
| body | TEXT | Comment body |
| path | TEXT | File path |
| created_at | TEXT | When comment was created |
| updated_at | TEXT | When comment was last updated |
| fetched_at | TEXT | When this record was fetched |

**Indexes:** `idx_rc_pr_id`, `idx_rc_author_id`, `idx_rc_created_at`

#### `issue_comments`
General comments on pull requests (via the Issues API).

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | GitHub comment ID |
| pull_request_id | INTEGER FK | References pull_requests(id) |
| author_id | INTEGER FK | References users(id) |
| body | TEXT | Comment body |
| created_at | TEXT | When comment was created |
| updated_at | TEXT | When comment was last updated |
| fetched_at | TEXT | When this record was fetched |

**Indexes:** `idx_ic_pr_id`, `idx_ic_author_id`, `idx_ic_created_at`

#### `governance_documents`
Version history of tracked governance files (CONTRIBUTING.md, OWNERS, etc.).

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| file_path | TEXT | Path within the repository |
| commit_sha | TEXT | Git commit SHA for this version |
| commit_date | TEXT | Commit timestamp |
| author_login | TEXT | Commit author's GitHub login |
| content | TEXT | Full file content at this commit |
| fetched_at | TEXT | When this record was fetched |

**Unique:** (file_path, commit_sha)
**Indexes:** `idx_gd_file_path`, `idx_gd_commit_date`

### Analysis Tables

#### `analysis_runs`
Reproducibility audit trail for each analysis execution.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| run_at | TEXT | When the analysis was run |
| bot_adoption_date | TEXT | Bot adoption date used |
| pre_window_months | INTEGER | Pre-period window size |
| post_window_months | INTEGER | Post-period window size |
| pre_start | TEXT | Pre-period start date |
| pre_end | TEXT | Pre-period end date |
| post_start | TEXT | Post-period start date |
| post_end | TEXT | Post-period end date |
| total_prs_pre | INTEGER | Total PRs in pre-period |
| total_prs_post | INTEGER | Total PRs in post-period |
| total_contributors_pre | INTEGER | Unique contributors pre |
| total_contributors_post | INTEGER | Unique contributors post |

#### `pr_metrics`
Per-PR computed metrics for RQ1, RQ2, and sentiment.

| Column | Type | Description |
|--------|------|-------------|
| pull_request_id | INTEGER PK | References pull_requests(id) |
| analysis_run_id | INTEGER FK | References analysis_runs(id) |
| period | TEXT | 'pre' or 'post' |
| author_id | INTEGER FK | References users(id) |
| contributor_type | TEXT | 'new' or 'established' |
| time_to_merge_hours | REAL | Hours from creation to merge |
| time_to_first_feedback_hours | REAL | Hours from creation to first non-author feedback |
| time_to_first_human_feedback_hours | REAL | Hours to first human (non-bot) feedback |
| time_to_first_human_review_hours | REAL | Hours to first formal human review |
| review_iterations | INTEGER | Count of CHANGES_REQUESTED reviews |
| was_rejected | INTEGER | 1 if closed without merge, NULL if open |
| human_review_comment_count | INTEGER | Human review comments |
| bot_review_comment_count | INTEGER | Bot review comments |
| human_issue_comment_count | INTEGER | Human issue comments |
| bot_issue_comment_count | INTEGER | Bot issue comments |
| total_human_comments | INTEGER | Sum of human comments |
| total_bot_comments | INTEGER | Sum of bot comments |
| additions | INTEGER | Lines added |
| deletions | INTEGER | Lines deleted |
| changed_files | INTEGER | Files changed |
| avg_human_sentiment | REAL | Average VADER compound score for human comments |
| avg_bot_sentiment | REAL | Average VADER compound score for bot comments |

**Indexes:** period, author_id, contributor_type, analysis_run_id

#### `contributor_metrics`
Per-contributor per-period summary.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| analysis_run_id | INTEGER FK | References analysis_runs(id) |
| user_id | INTEGER FK | References users(id) |
| period | TEXT | 'pre' or 'post' |
| contributor_type | TEXT | 'new' or 'established' |
| pr_count | INTEGER | Total PRs |
| merged_pr_count | INTEGER | Merged PRs |
| avg_time_to_merge_hours | REAL | Average merge time |
| avg_review_iterations | REAL | Average review iterations |
| returned_in_period | INTEGER | 1 if new contributor submitted 2+ PRs in the period |

**Unique:** (analysis_run_id, user_id, period)

#### `period_statistics`
Descriptive statistics per metric, period, and contributor type.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| analysis_run_id | INTEGER FK | References analysis_runs(id) |
| metric_name | TEXT | Metric being described |
| period | TEXT | 'pre' or 'post' |
| contributor_type | TEXT | 'all', 'new', or 'established' |
| n, mean, median, std_dev | REAL | Standard descriptive stats |
| min_val, max_val, q1, q3 | REAL | Distribution shape |

**Unique:** (analysis_run_id, metric_name, period, contributor_type)

#### `statistical_tests`
Hypothesis test results with FDR correction.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| analysis_run_id | INTEGER FK | References analysis_runs(id) |
| metric_name | TEXT | Metric tested |
| contributor_type | TEXT | 'all', 'new', or 'established' |
| test_name | TEXT | 'welch_t_test', 'mann_whitney_u', 'proportion_z_test' |
| statistic | REAL | Test statistic |
| p_value | REAL | Raw p-value |
| effect_size | REAL | Effect size value |
| effect_size_type | TEXT | 'cohens_d', 'odds_ratio', 'rank_biserial' |
| ci_lower, ci_upper | REAL | 95% confidence interval |
| pre_n, post_n | INTEGER | Sample sizes |
| significant | INTEGER | 1 if p < 0.05 |
| p_value_adjusted | REAL | Benjamini-Hochberg adjusted p-value |
| adjusted_significant | INTEGER | 1 if adjusted p < 0.05 |
| notes | TEXT | Normality test results, etc. |

**Unique:** (analysis_run_id, metric_name, contributor_type, test_name)

#### `comment_sentiments`
Per-comment VADER sentiment scores.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| comment_type | TEXT | 'review_comment' or 'issue_comment' |
| comment_id | INTEGER | Original comment ID |
| pull_request_id | INTEGER FK | References pull_requests(id) |
| author_id | INTEGER FK | References users(id) |
| period | TEXT | 'pre' or 'post' |
| is_bot | INTEGER | 1 if author is a bot |
| compound_score | REAL | VADER compound sentiment score (-1 to +1) |
| positive | REAL | Positive sentiment proportion |
| negative | REAL | Negative sentiment proportion |
| neutral | REAL | Neutral sentiment proportion |
| word_count | INTEGER | Words in preprocessed text |
| analysis_run_id | INTEGER FK | References analysis_runs(id) |

**Unique:** (analysis_run_id, comment_type, comment_id)
**Indexes:** `idx_cs_run_period`, `idx_cs_pr`

#### `sensitivity_results`
Results across multiple time window sizes.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| analysis_run_id | INTEGER FK | References analysis_runs(id) |
| window_months | INTEGER | Window size used |
| metric_name | TEXT | Metric name |
| contributor_type | TEXT | Contributor filter |
| pre_mean, post_mean | REAL | Period means |
| effect_size | REAL | Computed effect size |
| p_value | REAL | Test p-value |
| pre_n, post_n | INTEGER | Sample sizes |

**Unique:** (analysis_run_id, window_months, metric_name, contributor_type)

#### `governance_changes`
Computed diffs and classifications for governance document changes.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| analysis_run_id | INTEGER FK | References analysis_runs(id) |
| file_path | TEXT | Governance file path |
| change_date | TEXT | Commit date of the change |
| period | TEXT | 'pre', 'post', or NULL (outside window) |
| change_type | TEXT | 'created', 'modified', or 'deleted' |
| lines_added | INTEGER | Lines added in this diff |
| lines_removed | INTEGER | Lines removed in this diff |
| keywords_added | TEXT | Comma-separated governance keywords found in added lines |
| keywords_removed | TEXT | Comma-separated governance keywords found in removed lines |
| summary | TEXT | Human-readable change summary |
| bot_related | INTEGER | 1 if change content mentions bot/automation terms |
| diff_excerpt | TEXT | Truncated excerpt of added lines |
| category | TEXT | 'bot_integration', 'review_policy', 'ci_testing', 'contribution_guide', 'ownership', or 'other' |

**Unique:** (analysis_run_id, file_path, change_date)
**Indexes:** `idx_gc_run`, `idx_gc_period`

## Analysis Layer

### Bot Detection

The `bot_detection` module provides consistent bot identification across the pipeline. GitHub marks some accounts with `type='Bot'`, but many CI/automation accounts (e.g. `openshift-ci-robot`, `openshift-merge-robot`) are registered as `type='User'`. The module:

- Matches `type='Bot'` explicitly
- Matches login patterns: `%bot%`, `%robot%`, `%-ci-%` (case-insensitive)
- Exports `IS_BOT_SQL` and `NOT_BOT_SQL` fragments for use in SQL queries
- Exports `is_bot_user()` for Python-level filtering

### Metrics Computed

**RQ1 (Contributor Retention & Development Efficiency):**
- Within-period contributor retention rate (new contributors who submit 2+ PRs)
- Time-to-merge (hours from PR creation to merge)
- Time-to-first-feedback (hours from PR creation to first non-author comment/review)
- Time-to-first-human-feedback (same, but excluding bot responses)
- Time-to-first-human-review (hours to first formal human review submission)
- Review iterations (count of CHANGES_REQUESTED reviews as proxy)
- PR rejection rate (proportion of PRs closed without merge; open PRs excluded)

**RQ2 (Maintainer Behaviour):**
- Human review comments per PR
- Bot review comments per PR
- Human issue comments per PR
- Bot issue comments per PR
- Total human/bot comment ratios
- Average human sentiment per PR (VADER compound score)

**RQ3 (Governance Evolution):**
- Governance document change tracking across pre/post periods
- Lines added/removed per change
- Keyword detection (approval, reviewer, bot, CI, etc.)
- Category classification (bot_integration, review_policy, ci_testing, contribution_guide, ownership)
- Bot-relatedness flagging

### Sentiment Analysis

The `sentiment` module uses VADER (Valence Aware Dictionary and sEntiment Reasoner) to score comment sentiment. VADER is designed for short, social-media-style text, which shares characteristics with code review comments.

**Preprocessing** strips:
- Fenced and inline code blocks
- HTML tags, URLs, @mentions
- Markdown syntax (headers, bold/italic, image/link)
- Excessive whitespace

**Custom lexicon additions** for code review context:
- Positive: `lgtm` (+2.0), `shipit` (+2.0), `+1` (+1.5), `ack` (+1.0), `ptal` (+0.5)
- Negative: `-1` (-1.5), `nack` (-1.5), `nit` (-0.5), `wip` (-0.3)

Sentiment is computed per-comment (`comment_sentiments` table), then aggregated to per-PR averages (`avg_human_sentiment`, `avg_bot_sentiment` in `pr_metrics`).

### Contributor Classification

- **New**: No prior PRs in the repository before the current PR's creation date
- **Established**: At least one prior PR exists

**Retention**: A new contributor is considered "returned" if they submitted 2+ total PRs within the same period (their first 'new' PR plus at least one subsequent 'established' PR).

**Limitation:** Classification is relative to collected data. If collection doesn't cover full repo history, some "new" contributors may actually have older uncollected PRs.

### Statistical Methods

1. **Normality check**: Shapiro-Wilk test on each sample (subsampled to 5,000 if larger)
2. **If both normal**: Welch's t-test + Cohen's d with bootstrap 95% CI (1,000 iterations)
3. **If non-normal**: Mann-Whitney U + rank-biserial correlation
4. **Proportions**: Two-proportion z-test + odds ratio with 95% CI; Wilson score CI for individual rates
5. **Multiple testing correction**: Benjamini-Hochberg FDR applied to all tests within an analysis run
6. **Sensitivity**: Re-run tests across window sizes [1, 2, 3, 4, 6] months

### Analysis Orchestrator Flow

1. Initialize analysis tables (create + apply column migrations)
2. Read `bot_adoption_date` from `repositories` table (or from config override)
3. Compute time windows (pre/post boundaries)
4. Clear previous results, create `analysis_runs` record
5. Iterate PRs in windows, compute per-PR metrics (RQ1+RQ2), write `pr_metrics`
6. Compute comment sentiments, aggregate to per-PR averages
7. Update run totals
8. Aggregate per-contributor, write `contributor_metrics`; compute within-period retention
9. Compute descriptive stats, write `period_statistics`
10. Run hypothesis tests (continuous metrics + retention + rejection), write `statistical_tests`
11. Apply Benjamini-Hochberg FDR correction
12. Run sensitivity analysis across window sizes, write `sensitivity_results`
13. Run governance analysis (graceful skip if no data), write `governance_changes`

## Reporting Layer

### Dashboard Technology Decision: Streamlit (not Grafana)

The research proposal originally considered Grafana for dashboarding. After evaluation, Streamlit was chosen instead. The key factors:

| Criterion | Grafana | **Streamlit** |
|---|---|---|
| SQLite compatibility | Poor (needs plugin/proxy) | **Native (Python sqlite3)** |
| Python integration | None (Go-based) | **First-class** |
| Statistical computation | None (display only) | **scipy/pandas inline** |
| Interactive widgets | Config-heavy JSON | **Trivial Python widgets** |
| Academic export | Weak | **matplotlib/plotly export** |
| Deployment | Requires server + datasource | **`streamlit run app.py`** |
| Tekton static output | Cannot produce static reports | **Same code, headless mode** |

**Rationale:** Grafana is a monitoring tool -- it cannot run t-tests, compute Cohen's d, or do proportion tests. It needs a datasource adapter for SQLite and adds operational complexity disproportionate for a single-researcher project. Streamlit is pure Python, reads SQLite directly, supports dynamic parameter widgets, and the same analysis code can produce both interactive dashboards and static HTML/CSV exports.

### Dual-Mode Reporting Architecture

The reporting system serves two audiences:

1. **Tekton pipeline (headless):** `make report` generates static artifacts deterministically. These are the archival outputs suitable for the dissertation appendix and reproducibility.
2. **Local exploration (interactive):** `make dashboard` launches a Streamlit app for the researcher to interactively explore data, adjust parameters, and drill into results before finalising the analysis.

Both modes read from the same analysis tables in SQLite. The analysis code runs once (`make analyze`), and both reporting modes consume the precomputed results. The dashboard can also recompute statistics live when the researcher adjusts time-window sliders, by re-slicing raw `pr_metrics` data and calling the same `analysis.statistics` functions. This avoids duplicating statistical logic across static and interactive paths.

```
              ┌─────────────┐
              │  SQLite DB   │
              │ (analysis    │
              │  tables)     │
              └──────┬───────┘
                     │
           ┌─────────┴──────────┐
           │                    │
    ┌──────▼──────┐     ┌──────▼──────┐
    │ Static      │     │ Streamlit   │
    │ Report      │     │ Dashboard   │
    │ (headless)  │     │ (interactive)│
    └──────┬──────┘     └──────┬──────┘
           │                    │
    ┌──────▼──────┐     ┌──────▼──────┐
    │ reports/    │     │ localhost:  │
    │  report.html│     │  8501      │
    │  figures/   │     │ (browser)  │
    │  tables/    │     │            │
    └─────────────┘     └──────┬──────┘
                               │
                        ┌──────▼──────┐
                        │live_analysis│
                        │(dynamic     │
                        │ windows)    │
                        └─────────────┘
```

### Dashboard Design

**Entry point:** `src/setu_rp/reporting/dashboard.py`

**Global sidebar controls** (persist across all pages):
- **Contributor Type filter**: dropdown -- All / New / Established. Filters every metric, chart, and statistical test on the current page to the selected contributor subgroup.
- **Pre-window slider**: 1-12 months. Adjusts the pre-adoption observation period.
- **Post-window slider**: 1-12 months. Adjusts the post-adoption observation period.
- **Window status indicator**: shows "Using stored results" (green) when sliders match the analysis run, or "Live recomputation" (blue) with computed date ranges when sliders differ.
- **Analysis run metadata**: displays bot adoption date, run timestamp.

**Page navigation** via sidebar radio buttons. Each page is a self-contained module in `reporting/pages/` that receives `(conn, run_id, contributor_type, pre_months, post_months)` and renders its content.

### Live Recomputation (`reporting/live_analysis.py`)

When the time-window sliders differ from the stored analysis run, pages call `live_analysis` functions instead of reading from pre-computed DB tables. This module:

- Queries raw `pr_metrics` joined with `pull_requests` to filter by the dynamic date range
- Calls the same `analysis.statistics` functions (`descriptive_stats`, `choose_and_run_test`, `run_proportion_test`) used by the batch analyzer
- Caches results with `@st.cache_data` keyed on `(pre_months, post_months, contributor_type, run_id)` so repeated interactions are instant
- Falls back to stored DB results when sliders match the analysis run (zero overhead)

No new statistical logic is introduced -- the live path reuses the analysis layer's pure functions.

### Dashboard Pages

#### Page 1: Overview (`pages/overview.py`)
- Four summary metrics at top: total PRs pre/post, unique contributors pre/post
- Time windows table showing pre/post date ranges and bot adoption date
- Monthly PR activity bar chart (pre in blue, post in red) using plotly with hover tooltips (falls back to Streamlit native if plotly unavailable)
- Data quality section: counts of PRs missing merge time (unmerged) and missing feedback time (no external comments)

**Purpose:** Orientation -- gives the researcher a quick sense of dataset size, balance between periods, and any data quality concerns before diving into results.

#### Page 2: RQ1 Retention (`pages/rq1_retention.py`)
- Retention rate table: for each contributor type (All, New, Established), shows pre-period contributor count, how many returned in post-period, and the retention rate as a percentage
- Statistical test results table: z-test results for retention proportion comparison, with p-value, effect size (odds ratio), and significance indicator
- Monthly unique contributors line chart: visualises contributor engagement density over time, helping spot trends beyond the binary pre/post comparison

**Purpose:** Directly answers RQ1's core question -- did contributor retention change after bot adoption?

#### Page 3: RQ1 Efficiency (`pages/rq1_efficiency.py`)
- For each efficiency metric (time-to-merge, time-to-first-feedback, time-to-first-human-feedback, time-to-first-human-review, review iterations):
  - Descriptive statistics table (n, mean, median, std_dev, Q1, Q3) for pre and post
  - Plotly box plots showing value distributions (pre vs post) with hover details
  - Statistical test result with test name, p-value, effect size, and significance indicator (green/red)
- Rejection rate section: pre/post rejection percentages with proportion z-test result

**Purpose:** Answers whether the bot changed development workflow efficiency -- are PRs merged faster? Do contributors get feedback sooner? Are there fewer revision cycles?

#### Page 4: RQ2 Behaviour (`pages/rq2_behavior.py`)
- For each comment metric (total human, total bot, human review comments, bot review comments, avg human sentiment):
  - Descriptive statistics table
  - Statistical test result with effect size
- Comment composition over time: line chart showing average human vs bot comments per PR by month, making it easy to visualise how the comment mix shifted at the adoption boundary

**Purpose:** Answers RQ2 -- did maintainer review behaviour change? Are humans commenting less because the bot handles routine feedback? Or does bot introduction increase total comment volume?

#### Page 5: Sensitivity Analysis (`pages/sensitivity.py`)
- For each metric, two side-by-side plotly line charts:
  - Effect size vs window size (months)
  - p-value vs window size (months), with alpha = 0.05 reference line
- Full results table per metric showing pre_n, post_n, pre_mean, post_mean, effect_size, p_value across all tested window sizes

**Purpose:** Validates robustness of findings. If results hold across 1, 2, 3, 4, and 6-month windows, findings are robust. If significance depends heavily on a specific window size, this flags potential fragility for the dissertation discussion. Note: sensitivity results are always pre-computed and are not affected by the dynamic time-window sliders.

#### Page 6: Export (`pages/data_export.py`)
- Download buttons for each analysis table as CSV (pr_metrics, contributor_metrics, period_statistics, statistical_tests, sensitivity_results). When using dynamic time windows, exports contain live-recomputed data filtered to the adjusted window.
- APA-style statistical reporting strings: auto-generated text for each test result in proper academic format, e.g.:
  - t-test: "Time To Merge Hours (all): t(248) = -2.31, p = 0.021, d = -0.29, 95% CI [-0.54, -0.04]"
  - Mann-Whitney: "Review Iterations (new): U = 1523, p = 0.142, r = 0.12"
  - Proportion: "Retention Rate (all): z = 1.87, p = 0.061, OR = 1.42, 95% CI [0.98, 2.06]"

**Purpose:** Direct academic output -- the APA strings can be copy-pasted into the dissertation, and the CSVs enable further analysis in R/SPSS if needed.

### Static Report Outputs

The headless report generator (`reporting/static_report.py`) produces:

- `reports/report.html` -- self-contained HTML with inline CSS, embedded figure references, summary table, descriptive statistics table, and statistical tests table. No JavaScript dependencies; opens in any browser.
- `reports/figures/*.png` -- publication-quality charts at 150 DPI:
  - `timeline.png` -- monthly PR activity with adoption date marker
  - `{metric}_box.png` -- box plots for each continuous metric (pre vs post)
  - `retention_rates.png` -- grouped bar chart of retention by contributor type
  - `rejection_rates.png` -- grouped bar chart of rejection rates
  - `{metric}_sensitivity.png` -- effect size + p-value dual line charts per metric
- `reports/tables/*.csv` -- all analysis tables exported for external consumption

### Chart Libraries

- **matplotlib** for all static figure generation -- headless-compatible (uses `Agg` backend), produces deterministic output suitable for academic publication
- **plotly** (via `pip install -e ".[dashboard]"`) for interactive dashboard charts -- box/violin plots on efficiency and behaviour pages, bar charts on overview, line charts on sensitivity. Provides hover tooltips showing exact values. Falls back to Streamlit native charts if plotly is not installed.
- **Streamlit native charts** as fallback for simple visualisations when plotly is unavailable

## Pipeline Design

The pipeline consists of three sequential steps that map 1:1 between Tekton tasks and Makefile targets:

```
┌──────────┐     ┌──────────┐     ┌──────────┐
│ Collect  │────▶│ Analyze  │────▶│  Report  │
│ (Step 1) │     │ (Step 2) │     │ (Step 3) │
└──────────┘     └──────────┘     └──────────┘
                                        │
                                  ┌─────┴──────┐
                                  │ Dashboard  │
                                  │            │
                                  └────────────┘
```

- **Collect**: Fetches data from GitHub REST API into SQLite (PRs, reviews, comments, users, labels, governance docs)
- **Analyze**: Computes per-PR metrics, sentiment, contributor metrics, descriptive stats, hypothesis tests, FDR correction, sensitivity analysis, and governance analysis
- **Report**: Generates static HTML report, PNG figures, and CSV tables
- **Dashboard**: Interactive Streamlit app for local exploration (not in Tekton pipeline)

Each step is invoked via a shell script in `scripts/` that calls the Python CLI. This allows both Makefile targets and Tekton tasks to use the same entry points.

## Incremental Collection Strategy

### First Run
- Full fetch of all entities from the GitHub API
- Stores completion state in `sync_metadata` after each entity type

### Subsequent Runs
- Reads `last_sync` from `sync_metadata` for each entity type
- PRs: fetches `sort=updated&direction=desc`, stops when `updated_at <= last_sync`
- Review/issue comments: uses `since` parameter for server-side filtering
- Reviews: only fetches for PRs updated since last sync
- Users and labels: full fetch each run (small datasets)
- Governance docs: skips already-collected (file_path, commit_sha) pairs

### Resumability
- If collection is interrupted, `sync_metadata.last_page` tracks progress
- On restart, pagination resumes from `last_page` for the interrupted entity

## Configuration Management

Configuration follows a three-tier precedence (highest to lowest):

1. **CLI arguments** (`--db-path`, `--token`, `--owner`, `--name`, etc.)
2. **Environment variables** (`GITHUB_TOKEN`, `DB_PATH`, `REPO_OWNER`, `REPO_NAME`, `LOG_LEVEL`)
3. **YAML config file** (`config.yaml`)

Required fields: GitHub token (via env var or CLI) -- **only for the `collect` command**. The `analyze`, `report`, and `dashboard` commands do not require a token.

## Dependencies

### Core
- `requests>=2.31` -- GitHub API client
- `PyYAML>=6.0` -- Configuration
- `scipy>=1.11` -- Statistical tests (t-test, Mann-Whitney, Shapiro-Wilk)
- `pandas>=2.0` -- Data manipulation for reporting
- `matplotlib>=3.7` -- Static chart generation
- `python-dateutil>=2.8` -- `relativedelta` for time window computation
- `vaderSentiment>=3.3` -- VADER sentiment analysis for code review comments

### Optional (Dashboard)
- `streamlit>=1.28` -- Interactive web dashboard
- `plotly>=5.18` -- Interactive charts

### Development
- `pytest>=7.0` -- Testing (91 tests)
- `ruff>=0.1` -- Linting

## API Interaction Patterns

### Rate Limiting
- Reads `X-RateLimit-Remaining` and `X-RateLimit-Reset` from every response header
- Proactively sleeps when remaining requests drop below buffer (default: 100)
- Logs rate limit status every 50 requests

### Pagination
- Follows GitHub's `Link` header for `rel="next"`
- Configurable page size (default: 100, GitHub's maximum)
- Yields items one at a time via generator pattern

### Retry Strategy
- Exponential backoff on HTTP 5xx and 429 responses
- Maximum 3 retries per request
- Logs each retry attempt with reason

### Rate Budget Estimate (Initial Full Collection)

| Entity | Requests | Notes |
|--------|----------|-------|
| PR list | ~30 | ~3000 PRs at 100/page |
| Reviews | ~3000 | 1 request per PR |
| Review comments | ~30 | Repo-wide endpoint |
| Issue comments | ~30 | Repo-wide endpoint |
| Users/contributors | ~10 | Small dataset |
| Labels + repo | ~5 | Tiny |
| Governance docs | ~20 | Per-file commit history + content |
| **Total** | **~3125** | Well within 5000/hr limit |

## Known Limitations

- **Review iterations heuristic:** GitHub API has no explicit "iteration" concept. CHANGES_REQUESTED review count is used as proxy.
- **Contributor "newness" is relative to collected data:** if collection doesn't cover full repo history, some "new" contributors may have older uncollected PRs.
- **Sentiment analysis scope:** VADER is validated for short social-media-style text. Code review comments share some characteristics (short, informal) but also contain technical jargon that may not be well-represented in the VADER lexicon. Custom terms (lgtm, nit, etc.) partially address this.
- **Governance analysis depends on file existence:** if tracked governance files don't exist in the target repository, governance analysis is silently skipped.
