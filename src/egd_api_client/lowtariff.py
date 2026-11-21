"""Low-tariff (NT) periods given by the user, and Czech public holidays.

The EG.D OpenAPI does not say when a metering point was in the low tariff (no HDO,
distribution-rate or VT/NT register data for A/B meters), so NT periods are always entered
explicitly, e.g. ``"22:00-06:00,13:00-15:00"`` (optionally different for weekends).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Literal

from egd_api_client.timeutil import PRAGUE

HolidayRule = Literal["weekday", "weekend"]
Window = tuple[int, int]  # minutes from local midnight, [start, end), end ≤ 1440


class LowTariffError(ValueError):
    """Invalid schedule text."""


# ------------------------------------------------------------------------------------------
# Czech public holidays (zákon č. 245/2000 Sb.)
# ------------------------------------------------------------------------------------------
def _easter_sunday(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month = (h + m - 7 * n + 114) // 31
    day = (h + m - 7 * n + 114) % 31 + 1
    return date(year, month, day)


@lru_cache(maxsize=64)
def czech_holidays(year: int) -> frozenset[date]:
    fixed = [(1, 1), (5, 1), (5, 8), (7, 5), (7, 6), (9, 28), (10, 28), (11, 17),
             (12, 24), (12, 25), (12, 26)]  # fmt: skip
    easter = _easter_sunday(year)
    days = {date(year, m, d) for m, d in fixed}
    days.add(easter + timedelta(days=1))  # Easter Monday
    if year >= 2016:
        days.add(easter - timedelta(days=2))  # Good Friday
    return frozenset(days)


def is_czech_holiday(d: date) -> bool:
    return d in czech_holidays(d.year)


# ------------------------------------------------------------------------------------------
# schedule
# ------------------------------------------------------------------------------------------
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _minutes(text: str, *, end: bool = False) -> int:
    m = _TIME_RE.match(text.strip())
    if not m:
        raise LowTariffError(f"invalid time {text!r} (use HH:MM)")
    h, mi = int(m.group(1)), int(m.group(2))
    if end and (h, mi) in ((23, 59), (24, 0)):  # end of the day
        return 1440
    if h > 23 or mi > 59:
        raise LowTariffError(f"invalid time {text!r}")
    return h * 60 + mi


def _merge(windows: Iterable[Window]) -> tuple[Window, ...]:
    out: list[list[int]] = []
    for a, b in sorted(w for w in windows if w[1] > w[0]):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return tuple((a, b) for a, b in out)


def parse_hours(text: str) -> tuple[Window, ...]:
    """``"22:00-06:00, 13:00-15:00"`` → merged windows; ranges may cross midnight."""
    windows: list[Window] = []
    for part in filter(None, (p.strip() for p in text.split(","))):
        if "-" not in part:
            raise LowTariffError(f"invalid range {part!r} (use HH:MM-HH:MM)")
        a_txt, b_txt = part.split("-", 1)
        a, b = _minutes(a_txt), _minutes(b_txt, end=True)
        if a == b:
            raise LowTariffError(f"empty range {part!r}")
        if b > a:
            windows.append((a, b))
        else:  # crosses midnight
            windows += [(a, 1440), (0, b)]
    if not windows:
        raise LowTariffError("no low-tariff hours given")
    return _merge(windows)


def format_windows(windows: Sequence[Window]) -> str:
    def t(m: int) -> str:
        return f"{m // 60:02d}:{m % 60:02d}"

    return ", ".join(f"{t(a)}–{t(b)}" for a, b in windows) or "none"


@dataclass(frozen=True, slots=True)
class LowTariffSchedule:
    """NT windows for weekdays and for weekends (Saturday, Sunday).

    ``holidays``: ``weekday`` – a public holiday uses the windows of its actual weekday;
    ``weekend`` – it uses the weekend windows.
    """

    weekday: tuple[Window, ...]
    weekend: tuple[Window, ...]
    holidays: HolidayRule = "weekday"

    @classmethod
    def parse(
        cls, hours: str, weekend: str | None = None, holidays: HolidayRule = "weekday"
    ) -> LowTariffSchedule:
        wd = parse_hours(hours)
        return cls(wd, parse_hours(weekend) if weekend is not None else wd, holidays)

    def windows_for(self, d: date) -> tuple[Window, ...]:
        if d.isoweekday() >= 6 or (self.holidays == "weekend" and is_czech_holiday(d)):
            return self.weekend
        return self.weekday

    def low_fraction(self, start_utc: datetime, length: timedelta) -> float:
        """Share (0..1) of the interval [start, start+length) that falls into NT
        (local wall-clock time, so DST days are handled naturally)."""
        local = start_utc.astimezone(PRAGUE)
        d = local.date()
        a = local.hour * 60 + local.minute + local.second / 60
        b = a + length.total_seconds() / 60
        overlap = sum(max(0.0, min(b, wb) - max(a, wa)) for wa, wb in self.windows_for(d))
        if b > 1440:  # interval crosses local midnight
            nxt = self.windows_for(d + timedelta(days=1))
            overlap += sum(max(0.0, min(b - 1440, wb) - wa) for wa, wb in nxt if wa < b - 1440)
        return min(1.0, overlap / (b - a))

    def describe(self) -> str:
        text = f"NT {format_windows(self.weekday)}"
        if self.weekend != self.weekday:
            text += f"; Sat/Sun {format_windows(self.weekend)}"
        if self.holidays == "weekend":
            text += "; holidays as weekend"
        return text
