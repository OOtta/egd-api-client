"""Low-tariff (NT) periods: weekly time windows, Czech public holidays and EG.D HDO schedules.

The EG.D OpenAPI does not say when a metering point was in the low tariff: there is no HDO,
distribution-rate or VT/NT register data for A/B meters (VT/NT register profiles exist only
on the legacy C1 endpoint). NT periods therefore come from

* EG.D's public HDO switching-time data (``https://hdo.distribuce24.cz/casy``, the data
  behind egd.cz → "Časy platnosti nízkého tarifu"), selected by the HDO code / command
  printed on the HDO receiver or shown on the display of an interval (A/B) or smart meter, or
* a schedule given by the user (``"22:00-06:00,13:00-15:00"``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any, Literal

import requests

from egd_api_client.cache import JsonCache
from egd_api_client.http import DEFAULT_TIMEOUT, FetchError
from egd_api_client.timeutil import PRAGUE

HDO_TIMES_URL = "https://hdo.distribuce24.cz/casy"
HDO_REGION_URL = "https://hdo.distribuce24.cz/region"

#: Regions of classic HDO receivers (3-digit codes in the west, AxByDPz in the east).
CLASSIC_REGIONS = ("ZAPAD", "VYCHOD", "VERSACOM")

HolidayRule = Literal["auto", "sunday", "weekday"]
Window = tuple[int, int]  # minutes from local midnight, [start, end), end ≤ 1440


class LowTariffError(ValueError):
    """Invalid schedule text or an HDO code that cannot be resolved."""


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
# schedule model
# ------------------------------------------------------------------------------------------
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")


def _minutes(text: str, *, end: bool = False) -> int:
    m = _TIME_RE.match(text.strip())
    if not m:
        raise LowTariffError(f"invalid time {text!r} (use HH:MM)")
    h, mi = int(m.group(1)), int(m.group(2))
    if end and (h, mi) == (23, 59):  # EG.D writes the end of the day as 23:59
        return 1440
    if h == 24 and mi == 0:
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
    return _merge(windows)


def format_windows(windows: Sequence[Window]) -> str:
    def t(m: int) -> str:
        return f"{m // 60:02d}:{m % 60:02d}"

    return ", ".join(f"{t(a)}–{t(b)}" for a, b in windows) or "none"


@dataclass(frozen=True, slots=True)
class WeeklySchedule:
    """NT windows per ISO weekday (1 = Monday … 7 = Sunday)."""

    days: dict[int, tuple[Window, ...]]

    @classmethod
    def uniform(cls, all_days: str, weekend: str | None = None) -> WeeklySchedule:
        wd = parse_hours(all_days)
        we = parse_hours(weekend) if weekend is not None else wd
        return cls({i: (we if i >= 6 else wd) for i in range(1, 8)})


@dataclass(frozen=True, slots=True)
class Validity:
    """Validity of an HDO record: a dated range or a range repeating every year."""

    start: tuple[int, int]  # (month, day)
    end: tuple[int, int]
    years: tuple[int, int] | None = None  # None = every year

    def contains(self, d: date) -> bool:
        if self.years is not None:
            a = date(self.years[0], *self.start)
            b = date(self.years[1], *self.end)
            return a <= d <= b
        md = (d.month, d.day)
        if self.start <= self.end:
            return self.start <= md <= self.end
        return md >= self.start or md <= self.end  # wraps over New Year

    def describe(self) -> str:
        a = f"{self.start[1]:02d}.{self.start[0]:02d}."
        b = f"{self.end[1]:02d}.{self.end[0]:02d}."
        if self.years is None:
            return f"every year {a}–{b}"
        return f"{a}{self.years[0]}–{b}{self.years[1]}"


@dataclass(slots=True)
class LowTariffSchedule:
    """When the low tariff (NT) applies.

    ``periods`` are tried in order; the first whose validity contains the day wins.
    ``holidays``: ``sunday`` – Czech public holidays use the Sunday windows; ``weekday`` –
    they use their actual weekday.
    """

    periods: list[tuple[Validity | None, WeeklySchedule]]
    holidays: Literal["sunday", "weekday"] = "weekday"
    source: str = ""
    uncovered: set[date] = field(default_factory=set)

    def windows_for(self, d: date) -> tuple[Window, ...] | None:
        for validity, weekly in self.periods:
            if validity is None or validity.contains(d):
                wd = 7 if self.holidays == "sunday" and is_czech_holiday(d) else d.isoweekday()
                return weekly.days.get(wd, ())
        return None

    def low_fraction(self, start_utc: datetime, length: timedelta) -> float:
        """Share (0..1) of the interval [start, start+length) that falls into NT."""
        local = start_utc.astimezone(PRAGUE)
        d = local.date()
        windows = self.windows_for(d)
        if windows is None:
            self.uncovered.add(d)
            return 0.0
        a = local.hour * 60 + local.minute + local.second / 60
        b = a + length.total_seconds() / 60
        overlap = 0.0
        for wa, wb in windows:
            overlap += max(0.0, min(b, wb) - max(a, wa))
        if b > 1440:  # interval crosses local midnight
            nxt = self.windows_for(d + timedelta(days=1)) or ()
            overlap += sum(max(0.0, min(b - 1440, wb) - wa) for wa, wb in nxt if wa < b - 1440)
        return min(1.0, overlap / (b - a))

    def describe(self) -> str:
        return self.source


def manual_schedule(
    hours: str, weekend: str | None = None, holidays: HolidayRule = "auto"
) -> LowTariffSchedule:
    weekly = WeeklySchedule.uniform(hours, weekend)
    src = f"NT {format_windows(weekly.days[1])}"
    if weekend is not None:
        src += f"; Sat/Sun {format_windows(weekly.days[7])}"
    rule: Literal["sunday", "weekday"] = "sunday" if holidays == "sunday" else "weekday"
    if rule == "sunday":
        src += "; holidays as Sunday"
    return LowTariffSchedule([(None, weekly)], holidays=rule, source=src)


# ------------------------------------------------------------------------------------------
# EG.D HDO data
# ------------------------------------------------------------------------------------------
def _validity(rec: dict[str, Any]) -> Validity:
    od, do = rec["od"], rec["do"]
    start = (int(od["mesic"]), int(od["den"]))
    end = (int(do["mesic"]), int(do["den"]))
    y1, y2 = int(od["rok"]), int(do["rok"])
    if y1 == 9999:
        return Validity(start, end)
    return Validity(start, end, (y1, y2 if y2 >= y1 else y1 + 1))


def _weekly(rec: dict[str, Any], label: str) -> WeeklySchedule:
    days: dict[int, list[Window]] = {}
    for sazba in rec.get("sazby", []):
        if sazba.get("sazba") != label:
            continue
        for day in sazba.get("dny", []):
            for cas in day.get("casy", []):
                a, b = _minutes(cas["od"][:5]), _minutes(cas["do"][:5], end=True)
                if b > a:
                    days.setdefault(int(day["denVTydnu"]), []).append((a, b))
                elif b < a:  # not seen in the data, but handle it like a midnight crossing
                    days.setdefault(int(day["denVTydnu"]), []).append((a, 1440))
    return WeeklySchedule({k: _merge(v) for k, v in days.items()})


def _matches(rec: dict[str, Any], code: str) -> bool:
    c = code.strip().replace("-", "_").casefold()
    keys = ("kodHdo_A", "kodHdo_B", "kodHdo_C", "kodHdo_D", "kodHdo_E", "skupinaPovelu")
    return any(str(rec.get(k, "")).casefold() == c for k in keys)


class HdoClient:
    """EG.D public HDO switching times (no credentials needed; cached for a day)."""

    def __init__(self, session: requests.Session, cache: JsonCache, today: date | None = None):
        self.session = session
        self.cache = cache
        self.today = today or date.today()

    def _get(self, url: str, name: str) -> list[dict[str, Any]]:
        cached = self.cache.get("hdo", name)
        if cached and cached.get("fetched") == self.today.isoformat():
            return cached["data"]
        try:
            resp = self.session.get(url, timeout=DEFAULT_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            if cached:
                return cached["data"]
            raise FetchError(f"EG.D HDO data unavailable ({url}): {exc}") from exc
        self.cache.set("hdo", name, {"fetched": self.today.isoformat(), "data": data})
        return data

    def records(self) -> list[dict[str, Any]]:
        return self._get(HDO_TIMES_URL, "casy")

    def region_for_psc(self, psc: str) -> str:
        p = psc.replace(" ", "")
        regions = {
            r.get("Region") for r in self._get(HDO_REGION_URL, "region") if r.get("PSC") == p
        }
        regions.discard(None)
        if not regions:
            raise LowTariffError(f"PSČ {psc} not found in EG.D's HDO region list")
        if len(regions) > 1:
            raise LowTariffError(f"PSČ {psc} maps to several HDO regions: {sorted(regions)}")
        return regions.pop()

    def find(self, code: str, region: str | None = None) -> list[dict[str, Any]]:
        recs = [r for r in self.records() if _matches(r, code)]
        if region:
            recs = [r for r in recs if r.get("region", "").casefold() == region.casefold()]
        return recs

    def schedule(
        self,
        code: str,
        *,
        region: str | None = None,
        psc: str | None = None,
        relay: str | None = None,
        holidays: HolidayRule = "auto",
    ) -> LowTariffSchedule:
        if psc and not region:
            region = self.region_for_psc(psc)
        recs = self.find(code, region)
        if not recs:
            where = f" in region {region}" if region else ""
            raise LowTariffError(f"HDO code {code!r} not found{where} in EG.D's HDO data")
        regions = sorted({r["region"] for r in recs})
        if len(regions) > 1:
            raise LowTariffError(
                f"HDO code {code!r} exists in several regions ({', '.join(regions)}); "
                "add --hdo-region or --psc"
            )
        labels = sorted({s["sazba"] for r in recs for s in r.get("sazby", []) if s.get("sazba")})
        if relay is None:
            if len(labels) > 1:
                raise LowTariffError(
                    f"HDO code {code!r} has several schedules (relays): "
                    f"{'; '.join(labels)} – choose one with --hdo-relay"
                )
            relay = labels[0]
        elif relay not in labels:
            raise LowTariffError(f"--hdo-relay {relay!r} not among: {'; '.join(labels)}")
        rule: Literal["sunday", "weekday"]
        if holidays == "auto":
            rule = "sunday" if regions[0] in CLASSIC_REGIONS else "weekday"
        else:
            rule = holidays
        recs = [r for r in recs if any(z.get("sazba") == relay for z in r.get("sazby", []))]
        periods = [(_validity(r), _weekly(r, relay)) for r in recs]
        # dated records first (they override yearly patterns), then the yearly ones
        periods.sort(key=lambda p: p[0] is not None and p[0].years is None)
        src = f"EG.D HDO {code} (region {regions[0]}, {relay}; holidays as {rule})"
        return LowTariffSchedule(periods, holidays=rule, source=src)
