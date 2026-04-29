# setu-rp

A research framework for studying the impact of LLM-based code-review bots on open-source contributor retention and maintainer behaviour. It collects GitHub pull request data, computes per-PR and per-contributor metrics, runs hypothesis tests, and produces publication-ready reports.

The framework was built for an MSc dissertation studying [openshift/hypershift](https://github.com/openshift/hypershift) before and after [CodeRabbit](https://coderabbit.ai/) adoption, but it is designed to work with **any public GitHub repository** that has adopted an LLM review bot.

## What it measures

**RQ1 -- Contributor retention and development efficiency**
- Within-period contributor retention rate
- Time to merge (hours)
- Time to first feedback / first human feedback / first human review (hours)
- Review iterations (CHANGES_REQUESTED count as proxy)
- PR rejection rate

**RQ2 -- Maintainer review behaviour**
- Human vs bot comment counts (review comments and issue comments)
- Comment sentiment (VADER, with code-review-specific lexicon)

**RQ3 -- Governance evolution**
- Tracked governance document diffs (CONTRIBUTING.md, OWNERS, etc.)
- Keyword detection, category classification, bot-relatedness flagging

All metrics are compared across a **pre-adoption** and **post-adoption** period using Welch's t-test or Mann-Whitney U (chosen automatically via Shapiro-Wilk normality check), proportion z-tests for rates, Cohen's d / rank-biserial effect sizes, bootstrap confidence intervals, and Benjamini-Hochberg FDR correction. A sensitivity analysis re-runs every test across configurable window sizes.

## Quick start

```bash
# 1. Clone and set up
git clone <repo-url> && cd setu-rp
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[dev]"          # core + pytest/ruff
.venv/bin/pip install -e ".[dashboard]"    # optional: streamlit + plotly

# 2. Configure (edit config.yaml -- see "Adapting to another repository" below)

# 3. Collect data from GitHub
export GITHUB_TOKEN=ghp_...
make collect

# 4. Analyse and report
make analyze
make report                   # static HTML + PNG + CSV in reports/
make dashboard                # interactive Streamlit app on localhost:8501
```

Or run the full pipeline in one step:

```bash
export GITHUB_TOKEN=ghp_...
make pipeline                 # collect -> analyze -> report
```

## Adapting to another repository

The framework is not hard-coded to openshift/hypershift. To study a different repository:

### 1. Edit `config.yaml`

```yaml
repository:
  owner: "your-org"           # GitHub org or user
  name: "your-repo"           # repository name

study:
  bot_adoption_date: "2025-08-26T00:00:00Z"   # when the bot started reviewing
  pre_window_months: 6        # observation window before adoption
  post_window_months: 6       # observation window after adoption
  sensitivity_windows: [1, 2, 3, 4, 6]        # window sizes for robustness checks

storage:
  db_path: "data/your-repo.db"

governance:
  tracked_files:              # governance docs to track (adjust to your repo)
    - "CONTRIBUTING.md"
    - ".github/PULL_REQUEST_TEMPLATE.md"
    - "OWNERS"
```

### 2. Set your GitHub token

```bash
export GITHUB_TOKEN=ghp_...   # needs read access to the target repo
```

### 3. Determine the bot adoption date

The adoption date is the key parameter. You need the date when the LLM review bot began **sustained** activity on the repository (not a one-off trial). The `collect` step discovers bot activity automatically, but you should verify and set the date in `config.yaml`.

Tips:
- Search the repo's PR comments for the bot's username (e.g. `coderabbitai`, `copilot`)
- Look for the first month with consistent bot review activity (not just a single PR)
- The date should be the start of sustained usage, not the very first bot comment

### 4. Run the pipeline

```bash
make collect                  # fetches all PRs, reviews, comments, users, labels
make analyze                  # computes metrics, statistics, sensitivity
make report                   # generates HTML report + charts + CSVs
```

### 5. (Optional) Adjust bot detection

The framework detects bots by GitHub user type (`Bot`) and login patterns (`%bot%`, `%robot%`, `%-ci-%`). If your repository uses bots with unusual usernames, update the patterns in `src/setu_rp/analysis/bot_detection.py`.

## Commands

```bash
# Via Makefile (recommended)
make venv          # create venv and install dependencies
make init-db       # initialize empty database
make collect       # collect data from GitHub (requires GITHUB_TOKEN)
make analyze       # run analysis pipeline
make report        # generate static reports
make dashboard     # launch Streamlit dashboard
make pipeline      # collect -> analyze -> report
make test          # run tests (91 tests)
make lint          # run ruff linter

# Via CLI directly
.venv/bin/python -m setu_rp.cli --help
.venv/bin/python -m setu_rp.cli --db-path data/myrepo.db collect --owner myorg --name myrepo
.venv/bin/python -m setu_rp.cli analyze
.venv/bin/python -m setu_rp.cli report --output-dir reports --format both
.venv/bin/python -m setu_rp.cli dashboard --port 8501
```

CLI arguments override environment variables, which override `config.yaml`.

## Project structure

```
src/setu_rp/
  cli.py                     # argparse entry point (init-db, collect, analyze, report, dashboard)
  config.py                  # Config loader (CLI > env vars > config.yaml)
  db/
    connection.py            # SQLite context manager (WAL mode, foreign keys)
    schema.py                # Collection table DDL + governance_documents
    models.py                # Data classes
  collection/
    client.py                # GitHub REST client (pagination, rate limiting, retry)
    collector.py             # Collection orchestrator (incremental sync)
    pull_requests.py         # PR collection
    reviews.py               # Review collection
    comments.py              # Issue comment collection
    users.py                 # User collection
    labels.py                # Label collection
    governance.py            # Governance document version history collection
  analysis/
    analyzer.py              # Analysis orchestrator (run_analysis entry point)
    time_windows.py          # Pre/post period computation
    contributors.py          # Contributor classification (new/established) + retention
    metrics_rq1.py           # TTM, TTFF, TTFHF, TTFHR, iterations, rejection
    metrics_rq2.py           # Human/bot comment counts
    bot_detection.py         # Bot user identification (type + login patterns)
    sentiment.py             # VADER sentiment analysis with code-review lexicon
    governance.py            # Governance document diff analysis
    statistics.py            # Welch t-test, Mann-Whitney, proportion z-test, Cohen's d,
                             # rank-biserial, bootstrap CI, Wilson CI, Benjamini-Hochberg FDR
    schema.py                # Analysis table DDL + migrations
  reporting/
    static_report.py         # Headless HTML + CSV + PNG generator
    charts.py                # matplotlib chart functions
    dashboard.py             # Streamlit app entry point
    live_analysis.py         # Live recomputation for dynamic time windows
    pages/                   # 6 dashboard pages (overview, retention, efficiency,
                             #   behaviour, sensitivity, export)
tests/                       # 91 pytest tests
scripts/                     # Shell wrappers for Make/Tekton
tekton/                      # Tekton pipeline + task manifests
data/                        # .gitignored -- holds the SQLite database
config.yaml                  # Study parameters
```

## Database

A single SQLite database (WAL mode, foreign keys ON) serves as the interface between pipeline stages. No intermediate files.

**Collection tables:** `repositories`, `pull_requests`, `reviews`, `review_comments`, `issue_comments`, `users`, `labels`, `pull_request_labels`, `sync_metadata`, `governance_documents`

**Analysis tables:** `analysis_runs`, `pr_metrics`, `contributor_metrics`, `period_statistics`, `statistical_tests`, `comment_sentiments`, `sensitivity_results`, `governance_changes`

Collection is incremental -- the `sync_metadata` table tracks the last sync timestamp and page per entity type, so re-running `collect` only fetches new/updated data.

## Reports and dashboard

`make report` produces static artifacts in `reports/`:
- `report.html` -- self-contained HTML summary
- `figures/*.png` -- publication-quality charts (box plots, bar charts, sensitivity plots, timeline)
- `tables/*.csv` -- all analysis tables exported for use in R/SPSS/Excel

`make dashboard` launches an interactive Streamlit app with:
- Adjustable pre/post time windows (live recomputation of statistics)
- Contributor type filtering (all / new / established)
- Six pages: overview, retention, efficiency, behaviour, sensitivity, data export
- APA-formatted statistical reporting strings ready for copy-paste into manuscripts

## Tech stack

- **Python 3.11+** (developed on 3.14)
- **SQLite** -- data storage (WAL mode, foreign keys)
- **requests** -- GitHub REST API
- **scipy / numpy** -- statistical tests
- **pandas** -- data manipulation
- **matplotlib** -- static charts
- **vaderSentiment** -- comment sentiment analysis
- **streamlit + plotly** -- interactive dashboard (optional)
- **pytest + ruff** -- testing and linting
- **Docker + Tekton** -- containerised pipeline (optional)

## Requirements

- Python 3.11 or later
- A GitHub personal access token with read access to the target repository (for `collect` only)
- ~3,100 API requests for initial collection of a ~3,000-PR repository (well within GitHub's 5,000/hour limit)

## License

This project is part of an MSc dissertation at South East Technological University (SETU).
