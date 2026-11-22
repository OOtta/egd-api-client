"""Czech National Bank (ČNB) daily EUR/CZK fixing.

Source: the yearly text file published by ČNB, e.g.
https://www.cnb.cz/en/financial-markets/foreign-exchange-market/central-bank-exchange-rate-fixing/central-bank-exchange-rate-fixing/year.txt?year=2025

Rule used (matches how OTE converted EUR prices to CZK before October 2025, verified on
sample days): the rate fixed on the delivery day, or the last fixing before it for weekends
and holidays.
"""

from __future__ import annotations

import bisect
from dataclasses import asdict, dataclass
from datetime import date, datetime

import requests

from egd_api_client.cache import JsonCache
from egd_api_client.http import DEFAULT_TIMEOUT, FetchError
from egd_api_client.timeutil import local_today

CNB_YEAR_URL = (
    "https://www.cnb.cz/en/financial-markets/foreign-exchange-market/"
    "central-bank-exchange-rate-fixing/central-bank-exchange-rate-fixing/year.txt"
)


def parse_year_txt(text: str, currency: str = "EUR") -> dict[str, float]:
    """Parse ČNB ``year.txt`` → {"YYYY-MM-DD": CZK per 1 unit}.

    The header row (``Date|1 AUD|...``) may repeat mid-file when the currency list changes.
    """
    rates: dict[str, float] = {}
    col: int | None = None
    amount = 1.0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split("|")
        if parts[0] == "Date":
            col = None
            for i, head in enumerate(parts):
                bits = head.split()
                if len(bits) == 2 and bits[1] == currency:
                    col, amount = i, float(bits[0])
            continue
        if col is None or col >= len(parts):
            continue
        d = datetime.strptime(parts[0], "%d.%m.%Y").date()
        rates[d.isoformat()] = float(parts[col].replace(",", ".")) / amount
    return rates


#: version of the per-year ČNB cache document (``cnb/YYYY.json``)
CACHE_FORMAT = 2


@dataclass
class CnbCacheStats:
    cached_years: int = 0
    downloaded_years: int = 0
    requests: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass
class _Year:
    rates: dict[str, float]
    fetched_on: date | None  # None = unknown (legacy cache file)

    def complete(self, year: int) -> bool:
        """Fetched after the year ended → immutable."""
        if self.fetched_on is not None:
            return self.fetched_on.year > year
        # legacy file: complete if it reaches the last business days of December
        return bool(self.rates) and max(self.rates) >= f"{year}-12-27"


def _valid_rates(value: object) -> dict[str, float] | None:
    if not isinstance(value, dict) or not value:
        return None
    out: dict[str, float] = {}
    for k, v in value.items():
        try:
            date.fromisoformat(k)
        except (TypeError, ValueError):
            return None
        if not isinstance(v, int | float) or isinstance(v, bool) or v <= 0:
            return None
        out[k] = float(v)
    return out


class CnbRates:
    """ČNB EUR/CZK fixings. Cache: ``cnb/YYYY.json`` = ``{"format": 2, "year", "fetched_on",
    "rates": {"YYYY-MM-DD": rate}}``; a past year fetched after it ended is never re-downloaded,
    the current year only when a needed day is newer than the last cached fixing."""

    def __init__(self, session: requests.Session, cache: JsonCache, today: date | None = None):
        self.session = session
        self.cache = cache
        self.today = today or local_today()
        self._years: dict[int, dict[str, float]] = {}
        self.stats = CnbCacheStats()

    def _read_cache(self, year: int) -> _Year | None:
        raw = self.cache.get("cnb", str(year))
        if raw is None:
            return None
        if isinstance(raw, dict) and raw.get("format") == CACHE_FORMAT:
            rates = _valid_rates(raw.get("rates"))
            try:
                fetched = date.fromisoformat(raw["fetched_on"])
            except (KeyError, TypeError, ValueError):
                rates = None
            if rates is not None:
                return _Year(rates, fetched)
        elif isinstance(raw, dict) and "format" not in raw:
            rates = _valid_rates(raw)  # format 1: plain {"YYYY-MM-DD": rate}
            if rates is not None:
                return _Year(rates, None)
        self.cache.warn(f"ČNB cache file for {year} is invalid; downloading the rates again.")
        return None

    def _stale(self, year: int, cached: _Year | None, need: date | None) -> bool:
        if cached is None:
            return True
        if cached.complete(year):
            return False
        if need is None:  # previous-year lookup needs the year's last fixing
            return True
        if need.isoformat() <= max(cached.rates):
            return False
        # a newer fixing may exist: refetch once per day, always for today/future days
        return cached.fetched_on is None or cached.fetched_on < need or need >= self.today

    def _load_year(self, year: int, need: date | None = None) -> dict[str, float]:
        if year in self._years:
            return self._years[year]
        cached = self._read_cache(year)
        if not self._stale(year, cached, need):
            assert cached is not None
            self.stats.cached_years += 1
            self._years[year] = cached.rates
            return cached.rates
        self.stats.requests += 1
        try:
            resp = self.session.get(CNB_YEAR_URL, params={"year": year}, timeout=DEFAULT_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as exc:
            if cached:
                self.cache.warn(
                    f"Cannot download ČNB exchange rates for {year} ({exc}); using cached rates."
                )
                self.stats.cached_years += 1
                self._years[year] = cached.rates
                return cached.rates
            raise FetchError(f"Cannot download ČNB exchange rates for {year}: {exc}") from exc
        rates = parse_year_txt(resp.text)
        if not rates:
            raise FetchError(f"ČNB returned no EUR rates for {year}")
        self.stats.downloaded_years += 1
        if year <= self.today.year:  # never cache a future year
            self.cache.set(
                "cnb",
                str(year),
                {
                    "format": CACHE_FORMAT,
                    "year": year,
                    "fetched_on": self.today.isoformat(),
                    "rates": rates,
                },
            )
        self._years[year] = rates
        return rates

    def fixing(self, day: date) -> tuple[date, float]:
        """(fixing date, EUR/CZK) valid for ``day``.

        ČNB fixes rates every business day at ~14:30, valid for that day and any following
        weekend/holiday. OTE's ``GetDamIndexE.EurRate`` ("kurz ČNB pro daný den dodávky")
        equals exactly this value (checked for every day 2025-01..2026-11).
        """
        for year in (day.year, day.year - 1):
            rates = self._load_year(year, need=day if year == day.year else None)
            keys = sorted(rates)
            i = bisect.bisect_right(keys, day.isoformat())
            if i:
                return date.fromisoformat(keys[i - 1]), rates[keys[i - 1]]
        raise FetchError(f"No ČNB EUR/CZK rate found on or before {day}")

    def eur_czk(self, day: date) -> float:
        """EUR/CZK fixing valid for ``day`` (last fixing on or before it)."""
        return self.fixing(day)[1]
