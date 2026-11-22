"""Time helpers.

Everything internal is a timezone-aware UTC datetime marking an interval *start*.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TZ_HINT = (
    "Install the time-zone database: `pip install tzdata` "
    "(pipx: `pipx inject egd-api-client tzdata`)."
)


def load_zone(key: str = "Europe/Prague") -> ZoneInfo:
    """Load a time zone, with an actionable message when no tz database is available."""
    try:
        return ZoneInfo(key)
    except ZoneInfoNotFoundError as exc:
        raise ZoneInfoNotFoundError(
            f"Time zone {key!r} not found: this system has no time-zone database. {TZ_HINT}"
        ) from exc


PRAGUE = load_zone("Europe/Prague")
QUARTER = timedelta(minutes=15)
HOUR = timedelta(hours=1)


def local_midnight_utc(day: date) -> datetime:
    """UTC instant of 00:00 local (Europe/Prague) time on ``day``."""
    return datetime(day.year, day.month, day.day, tzinfo=PRAGUE).astimezone(UTC)


def local_day_bounds_utc(start: date, end_inclusive: date) -> tuple[datetime, datetime]:
    """UTC half-open range [start 00:00 local, (end+1) 00:00 local)."""
    return local_midnight_utc(start), local_midnight_utc(end_inclusive + timedelta(days=1))


def period_start_utc(day: date, index: int, length: timedelta) -> datetime:
    """Start of the ``index``-th (1-based) period of a delivery day.

    OTE numbers periods consecutively from local midnight, so a DST day has 23/25 hours
    (92/100 quarter-hours). Counting elapsed real time from local midnight handles that.
    """
    return local_midnight_utc(day) + (index - 1) * length


def to_local(dt: datetime) -> datetime:
    return dt.astimezone(PRAGUE)


def local_date(dt: datetime) -> date:
    return dt.astimezone(PRAGUE).date()


def local_today() -> date:
    """Today's date in Prague (the market's delivery-day calendar)."""
    return datetime.now(UTC).astimezone(PRAGUE).date()


def iter_days(start: date, end_inclusive: date) -> Iterator[date]:
    d = start
    while d <= end_inclusive:
        yield d
        d += timedelta(days=1)


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def days_in_month(year: int, month: int) -> int:
    nxt = date(year + (month == 12), (month % 12) + 1, 1)
    return (nxt - date(year, month, 1)).days


def parse_iso_utc(value: str) -> datetime:
    """Parse ISO-8601 (``Z`` or offset); naive values are treated as UTC."""
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def format_api_utc(dt: datetime) -> str:
    """Format as EG.D expects: ``YYYY-MM-DDTHH:MM:00.000Z``."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:00.000Z")
