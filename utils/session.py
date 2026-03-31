"""
Trading session filter.

Session times are in UTC.  Only London and New York are active sessions;
the scanner skips pairs when no session is open (score -= 20 points).
"""

from datetime import datetime, time

# Session windows (UTC, inclusive)
SESSIONS: dict[str, dict[str, time]] = {
    "London": {
        "start": time(8, 0),
        "end": time(17, 0),
    },
    "New York": {
        "start": time(13, 0),
        "end": time(22, 0),
    },
}


def _now_utc() -> time:
    return datetime.utcnow().time()


def get_active_sessions(now: time | None = None) -> list[str]:
    """
    Return names of all currently active sessions.

    Args:
        now: UTC time to check (defaults to current UTC time).
             Inject a specific time in unit tests.
    """
    t = now or _now_utc()
    return [
        name
        for name, window in SESSIONS.items()
        if window["start"] <= t <= window["end"]
    ]


def is_session_active(now: time | None = None) -> bool:
    return len(get_active_sessions(now)) > 0


def primary_session(now: time | None = None) -> str:
    """Return the most specific active session name, or 'Off-hours'."""
    active = get_active_sessions(now)
    if not active:
        return "Off-hours"
    # If both London and New York overlap (13:00–17:00 UTC) prefer "London/NY"
    if len(active) > 1:
        return "London/NY overlap"
    return active[0]
