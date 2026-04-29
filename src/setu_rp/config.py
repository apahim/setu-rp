"""Configuration loader with YAML + env var + CLI arg precedence."""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Config:
    repo_owner: str = "openshift"
    repo_name: str = "hypershift"
    github_token: str = ""
    db_path: str = "data/hypershift.db"
    per_page: int = 100
    max_retries: int = 3
    rate_limit_buffer: int = 100
    log_level: str = "INFO"
    bot_adoption_date: str | None = None
    pre_window_months: int = 6
    post_window_months: int = 6
    sensitivity_windows: list[int] | None = None
    output_dir: str = "reports"
    report_format: str = "both"


def load_config(
    config_path: str = "config.yaml",
    cli_args: dict | None = None,
    require_token: bool = True,
) -> Config:
    """Load configuration with precedence: CLI args > env vars > YAML file.

    Args:
        config_path: Path to YAML config file.
        cli_args: CLI argument overrides.
        require_token: If True, raises ValueError when no GitHub token is set.
            Set to False for commands that don't need GitHub API access
            (analyze, report, dashboard).
    """
    cfg = {}

    # Layer 1: YAML config file
    config_file = Path(config_path)
    if config_file.exists():
        with open(config_file) as f:
            raw = yaml.safe_load(f) or {}
        cfg["repo_owner"] = raw.get("repository", {}).get("owner", "openshift")
        cfg["repo_name"] = raw.get("repository", {}).get("name", "hypershift")
        cfg["db_path"] = raw.get("storage", {}).get("db_path", "data/hypershift.db")
        cfg["per_page"] = raw.get("github", {}).get("per_page", 100)
        cfg["max_retries"] = raw.get("github", {}).get("max_retries", 3)
        cfg["rate_limit_buffer"] = raw.get("github", {}).get("rate_limit_buffer", 100)
        cfg["log_level"] = raw.get("logging", {}).get("level", "INFO")
        cfg["bot_adoption_date"] = raw.get("study", {}).get("bot_adoption_date")
        cfg["pre_window_months"] = raw.get("study", {}).get("pre_window_months", 3)
        cfg["post_window_months"] = raw.get("study", {}).get("post_window_months", 3)
        cfg["sensitivity_windows"] = raw.get("study", {}).get(
            "sensitivity_windows", [1, 2, 3, 4, 6]
        )
        reporting = raw.get("reporting", {})
        if reporting.get("output_dir"):
            cfg["output_dir"] = reporting["output_dir"]
        if reporting.get("format"):
            cfg["report_format"] = reporting["format"]

    # Layer 2: Environment variables override YAML
    env_map = {
        "GITHUB_TOKEN": "github_token",
        "DB_PATH": "db_path",
        "REPO_OWNER": "repo_owner",
        "REPO_NAME": "repo_name",
        "LOG_LEVEL": "log_level",
    }
    for env_key, cfg_key in env_map.items():
        val = os.environ.get(env_key)
        if val is not None:
            cfg[cfg_key] = val

    # Layer 3: CLI arguments override everything
    if cli_args:
        for key, val in cli_args.items():
            if val is not None:
                cfg[key] = val

    config = Config(**cfg)

    # Validate required fields
    if require_token and not config.github_token:
        raise ValueError(
            "GitHub token is required. Set GITHUB_TOKEN env var or pass --token."
        )

    return config
