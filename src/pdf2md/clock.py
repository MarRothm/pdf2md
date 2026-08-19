"""One source of truth for timestamps.

All persisted and returned times are ISO-8601 UTC with a `Z` suffix
(contracts/web-api.md). Millisecond precision keeps `?since=` polling exact when
several jobs change inside the same second.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def now() -> datetime:
    return datetime.now(UTC)


def to_iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def now_iso() -> str:
    return to_iso(now())


def parse_iso(value: str) -> datetime:
    """Parse a timestamp we or a client produced; naive values are read as UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def iso_ago(*, hours: float = 0, days: float = 0) -> str:
    return to_iso(now() - timedelta(hours=hours, days=days))
