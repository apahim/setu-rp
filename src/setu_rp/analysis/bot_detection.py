"""Bot detection helpers for filtering automated accounts.

GitHub marks some bot accounts with type='Bot', but many CI/automation
accounts (e.g. openshift-ci-robot, openshift-merge-robot) are registered
as type='User'.  This module provides consistent bot detection across
the analysis pipeline.
"""

# Login patterns that identify automated accounts registered as type='User'.
# Matches are case-insensitive substrings applied via SQL LIKE.
_BOT_LOGIN_PATTERNS = (
    "%bot%",
    "%robot%",
    "%-ci-%",
)

# SQL condition that identifies bot users.  Use with a JOIN on the users
# table aliased as ``u``:  ``JOIN users u ON ... WHERE`` + NOT_BOT_SQL
NOT_BOT_SQL = (
    "u.type != 'Bot'"
    + "".join(f" AND u.login NOT LIKE '{p}'" for p in _BOT_LOGIN_PATTERNS)
)

# Inverse: identifies bot users (type='Bot' OR login matches pattern).
IS_BOT_SQL = (
    "(u.type = 'Bot'"
    + "".join(f" OR u.login LIKE '{p}'" for p in _BOT_LOGIN_PATTERNS)
    + ")"
)


def is_bot_user(user_type: str, login: str) -> bool:
    """Return True if the user should be treated as a bot.

    Args:
        user_type: GitHub user type ('Bot' or 'User').
        login: GitHub login name.

    Returns:
        True if the account is a bot or matches known bot login patterns.
    """
    if user_type == "Bot":
        return True
    login_lower = login.lower()
    return any(
        _matches_like(login_lower, p.lower()) for p in _BOT_LOGIN_PATTERNS
    )


def _matches_like(value: str, pattern: str) -> bool:
    """Emulate SQL LIKE with leading/trailing % wildcards."""
    stripped = pattern.strip("%")
    if pattern.startswith("%") and pattern.endswith("%"):
        return stripped in value
    if pattern.startswith("%"):
        return value.endswith(stripped)
    if pattern.endswith("%"):
        return value.startswith(stripped)
    return value == stripped
