# CLAUDE.md -- setu-rp

## Project

MSc dissertation research framework studying the impact of LLM-based code-review bots on contributor retention in **openshift/hypershift**. The study compares pre- vs post-bot-adoption periods using statistical analysis. The framework can be pointed at any public GitHub repository by editing `config.yaml`.

**Bot adoption date:** 2025-08-26 (configured in `config.yaml`).
- First CodeRabbit comment was 2025-05-20 (a one-off trial on a shared-ingress PR, 26 comments total, then nothing in Jun/Jul).
- Sustained usage began 2025-08-26 (77 review + 83 issue comments in Aug, then 600+/month from Sep onward).
- We use 2025-08-26 as the adoption date because it marks the start of consistent, sustained bot activity.

## Tech Stack

- **Language:** Python 3.11+ (dev machine runs 3.14)
- **Data:** SQLite (WAL mode, foreign keys ON), REST via GitHub API
- **Analysis:** scipy, numpy, pandas, matplotlib, python-dateutil, vaderSentiment
- **Dashboard:** streamlit + plotly (optional `[dashboard]` extras)
- **Dev:** pytest, ruff
- **Build:** pyproject.toml with setuptools

## Commands

```bash
# Use the project venv for everything
.venv/bin/python -m pytest tests/ -v          # run tests (91 tests)
.venv/bin/ruff check src/ tests/              # lint
.venv/bin/python -m setu_rp.cli --help        # CLI entry point

# Or via Makefile (uses .venv automatically)
make test
make lint
make collect       # requires GITHUB_TOKEN
make analyze
make report
make dashboard
make pipeline      # collect -> analyze -> report
```

## Project Structure

```
src/setu_rp/
  config.py              # Config precedence: CLI > env vars > config.yaml
  cli.py                 # Subcommands: init-db, collect, analyze, report, dashboard
  db/
    connection.py        # get_connection(), WAL mode, foreign keys
    schema.py            # init_db() DDL (incl. governance_documents)
    models.py            # Data classes
  collection/
    client.py            # GitHubClient (pagination, rate limit, retry)
    collector.py         # run_collection() orchestrator
    pull_requests.py, reviews.py, comments.py, users.py, labels.py
    governance.py        # Governance doc version history collection
  analysis/
    analyzer.py          # run_analysis() orchestrator
    time_windows.py      # Pre/post period calculation
    contributors.py      # Contributor classification + within-period retention
    metrics_rq1.py       # TTM, TTFF, TTFHF, TTFHR, iterations, rejection rate
    metrics_rq2.py       # Human/bot comment counts
    bot_detection.py     # Bot user identification (type + login patterns, SQL helpers)
    sentiment.py         # VADER sentiment with code-review-specific lexicon
    governance.py        # Governance doc diff analysis, keyword detection, categorisation
    statistics.py        # Welch t-test, Mann-Whitney, proportion z-test, Cohen's d,
                         # rank-biserial, bootstrap CI, Wilson CI, Benjamini-Hochberg FDR
    schema.py            # Analysis tables DDL + column migrations
  reporting/
    static_report.py     # HTML + CSV + PNG generator
    charts.py            # matplotlib chart functions
    dashboard.py         # Streamlit app entry point
    live_analysis.py     # Live analysis for dashboard
    pages/               # 6 Streamlit pages (overview, retention, efficiency, behaviour, sensitivity, export)
tests/                   # pytest tests (91), fixtures in tests/fixtures/
scripts/                 # Shell wrappers for Make/Tekton
tekton/                  # Pipeline + task YAML manifests
data/                    # gitignored, holds hypershift.db
config.yaml              # Study parameters
```

## Database

- **Path:** `data/hypershift.db` (~139 MB)
- Collection tables: `pull_requests`, `reviews`, `review_comments`, `issue_comments`, `users`, `labels`, `pull_request_labels`, `repositories`, `sync_metadata`, `governance_documents`
- Analysis tables: `analysis_runs`, `pr_metrics`, `contributor_metrics`, `period_statistics`, `statistical_tests`, `comment_sentiments`, `sensitivity_results`, `governance_changes`
- Incremental sync via `sync_metadata` table
- Upserts use `INSERT OR REPLACE`

## Key Patterns

- `github_token` only required for `collect`, not for analyze/report/dashboard
- Bot detection: `type='Bot'` + login patterns (`%bot%`, `%robot%`, `%-ci-%`) via `bot_detection.py`
- Statistics: Shapiro-Wilk normality check -> Welch's t-test or Mann-Whitney U; proportion z-test for rates; Benjamini-Hochberg FDR correction
- Sentiment: VADER with code-review lexicon (lgtm, nit, shipit, etc.); preprocesses markdown/code/mentions
- Sensitivity analysis varies the window size (1, 2, 3, 4, 6 months)
- Dual-mode reporting: static (for Tekton pipeline) + interactive (Streamlit)
- Governance analysis: tracks CONTRIBUTING.md, OWNERS, PR templates; computes diffs, keywords, categories

## Ruff Config

- Target: py311, line-length: 100
