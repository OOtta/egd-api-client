"""Czech National Bank (ČNB) daily EUR/CZK fixing.

Source: the yearly text file published by ČNB, e.g.
https://www.cnb.cz/en/financial-markets/foreign-exchange-market/central-bank-exchange-rate-fixing/central-bank-exchange-rate-fixing/year.txt?year=2025

Rule used (matches how OTE converted EUR prices to CZK before October 2025, verified on
sample days): the rate fixed on the delivery day, or the last fixing before it for weekends
and holidays.
"""

from __future__ import annotations

import bisect
from datetime import date, datetime

import requests

from egd_spot.cache import JsonCache
from egd_spot.http import DEFAULT_TIMEOUT, FetchError

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


class CnbRates:
    def __init__(self, session: requests.Session, cache: JsonCache, today: date | None = None):
        self.session = session
        self.cache = cache
        self.today = today or date.today()
        self._years: dict[int, dict[str, float]] = {}

    def _load_year(self, year: int, need: date | None = None) -> dict[str, float]:
        if year in self._years:
            return self._years[year]
        key = str(year)
        cached = self.cache.get("cnb", key)
        stale = cached is None or (
            year >= self.today.year and need is not None and max(cached) < need.isoformat()
        )
        if stale:
            try:
                resp = self.session.get(
                    CNB_YEAR_URL, params={"year": year}, timeout=DEFAULT_TIMEOUT
                )
                resp.raise_for_status()
            except requests.RequestException as exc:
                if cached:
                    self._years[year] = cached
                    return cached
                raise FetchError(f"Cannot download ČNB exchange rates for {year}: {exc}") from exc
            rates = parse_year_txt(resp.text)
            if not rates:
                raise FetchError(f"ČNB returned no EUR rates for {year}")
            self.cache.set("cnb", key, rates)
            cached = rates
        self._years[year] = cached
        return cached

    def eur_czk(self, day: date) -> float:
        """EUR/CZK fixing valid for ``day`` (last fixing on or before it)."""
        for year in (day.year, day.year - 1):
            rates = self._load_year(year, need=day if year == day.year else None)
            keys = sorted(rates)
            i = bisect.bisect_right(keys, day.isoformat())
            if i:
                return rates[keys[i - 1]]
        raise FetchError(f"No ČNB EUR/CZK rate found on or before {day}")
