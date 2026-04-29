"""CLI entry point with subcommands: init-db, collect, analyze, report, dashboard."""

import argparse
import logging
import sys

from setu_rp.config import load_config
from setu_rp.db.connection import get_connection
from setu_rp.db.schema import init_db


def cmd_init_db(args):
    """Initialize the database schema."""
    config = load_config(
        config_path=args.config,
        cli_args={"db_path": args.db_path},
        require_token=False,
    )
    db_path = args.db_path or config.db_path
    with get_connection(db_path) as conn:
        init_db(conn)
    print(f"Database initialized at {db_path}")
    return 0


def cmd_collect(args):
    """Run data collection from GitHub."""
    config = load_config(
        config_path=args.config,
        cli_args={
            "db_path": args.db_path,
            "github_token": args.token,
            "repo_owner": args.owner,
            "repo_name": args.name,
        },
    )

    from setu_rp.collection.client import GitHubClient
    from setu_rp.collection.collector import run_collection

    client = GitHubClient(
        token=config.github_token,
        per_page=config.per_page,
        max_retries=config.max_retries,
        rate_limit_buffer=config.rate_limit_buffer,
    )

    with get_connection(config.db_path) as conn:
        init_db(conn)
        run_collection(client, conn, config.repo_owner, config.repo_name)

    return 0


def cmd_analyze(args):
    """Run analysis on collected data."""
    config = load_config(
        config_path=args.config,
        cli_args={"db_path": args.db_path},
        require_token=False,
    )

    from setu_rp.analysis.analyzer import run_analysis

    db_path = args.db_path or config.db_path
    with get_connection(db_path) as conn:
        run_analysis(
            conn,
            pre_months=config.pre_window_months,
            post_months=config.post_window_months,
            bot_adoption_date=config.bot_adoption_date,
            sensitivity_windows=config.sensitivity_windows,
        )

    print("Analysis complete.")
    return 0


def cmd_report(args):
    """Generate static reports."""
    config = load_config(
        config_path=args.config,
        cli_args={
            "db_path": args.db_path,
            "output_dir": args.output_dir,
            "report_format": args.format,
        },
        require_token=False,
    )

    from setu_rp.reporting.static_report import generate_report

    db_path = args.db_path or config.db_path
    with get_connection(db_path) as conn:
        generate_report(
            conn,
            output_dir=config.output_dir,
            report_format=config.report_format,
        )

    print(f"Reports written to {config.output_dir}/")
    return 0


def cmd_dashboard(args):
    """Launch the interactive Streamlit dashboard."""
    import subprocess

    config = load_config(
        config_path=args.config,
        cli_args={"db_path": args.db_path},
        require_token=False,
    )
    db_path = args.db_path or config.db_path
    port = args.port or "8501"

    dashboard_module = "src/setu_rp/reporting/dashboard.py"
    cmd = [
        sys.executable, "-m", "streamlit", "run", dashboard_module,
        "--server.port", port,
        "--", "--db-path", db_path,
    ]
    print(f"Launching dashboard at http://localhost:{port}")
    subprocess.run(cmd, check=True)
    return 0


def main():
    parser = argparse.ArgumentParser(
        prog="setu-rp",
        description="Research data analysis framework for LLM code-review bot impact study",
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--db-path", dest="db_path", help="Path to SQLite database")
    parser.add_argument("--token", help="GitHub personal access token")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # init-db
    subparsers.add_parser("init-db", help="Initialize the database schema")

    # collect
    collect_parser = subparsers.add_parser("collect", help="Collect data from GitHub")
    collect_parser.add_argument("--owner", help="Repository owner")
    collect_parser.add_argument("--name", help="Repository name")

    # analyze
    subparsers.add_parser("analyze", help="Run analysis on collected data")

    # report
    report_parser = subparsers.add_parser("report", help="Generate static reports")
    report_parser.add_argument("--output-dir", dest="output_dir", help="Output directory")
    report_parser.add_argument(
        "--format", choices=["html", "csv", "both"], default=None, help="Report format"
    )

    # dashboard
    dashboard_parser = subparsers.add_parser("dashboard", help="Launch interactive dashboard")
    dashboard_parser.add_argument("--port", default=None, help="Streamlit server port")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    commands = {
        "init-db": cmd_init_db,
        "collect": cmd_collect,
        "analyze": cmd_analyze,
        "report": cmd_report,
        "dashboard": cmd_dashboard,
    }

    try:
        sys.exit(commands[args.command](args))
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        logging.getLogger(__name__).exception("Unexpected error")
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
