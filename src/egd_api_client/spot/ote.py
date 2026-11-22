"""OTE-CR day-ahead market (DT/DAM) prices via OTE's public SOAP web service.

Service: https://www.ote-cr.cz/pw-data/services/PublicDataService (WSDL: ``?wsdl``)
Manual:  https://www.ote-cr.cz/cs/dokumentace/xsd-wsdl-manual-pubweb
         (``uzivatelsky-manual_webove_sluzby_ote_k.pdf``)

* Delivery days up to 2025-09-30: ``GetDamPriceE`` – hourly prices; ``InEur=false`` returns
  OTE's official CZK/MWh values directly, ``InEur=true`` OTE's EUR/MWh values (fetched only
  when EUR output is requested).
* Delivery days from 2025-10-01 (15-minute day-ahead market): ``GetDamPricePeriodE`` with
  ``PeriodResolution=PT15M`` – 15-minute ``Price`` and the hourly ``HourlyPrice`` in EUR/MWh.
  Converted to CZK with the ČNB fixing for the delivery day (see :mod:`egd_api_client.spot.cnb`).

Requests are limited to one calendar month; periods are numbered from local
midnight (23/25 hours or 92/100 quarter-hours on DST days).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Literal

import requests

from egd_api_client.cache import JsonCache
from egd_api_client.http import DEFAULT_TIMEOUT, FetchError
from egd_api_client.models import PricePoint
from egd_api_client.spot.cnb import CnbRates
from egd_api_client.timeutil import (
    HOUR,
    QUARTER,
    iter_days,
    local_midnight_utc,
    local_today,
    period_start_utc,
)

OTE_SERVICE_URL = "https://www.ote-cr.cz/pw-data/services/PublicDataService"
NS = "http://www.ote-cr.cz/schema/service/public"
QUARTER_HOUR_MARKET_START = date(2025, 10, 1)

PriceResolution = Literal["auto", "hourly"]


def _envelope(operation: str, fields: dict[str, str]) -> str:
    body = "".join(f"<pub:{k}>{v}</pub:{k}>" for k, v in fields.items())
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        f'xmlns:pub="{NS}"><soapenv:Header/><soapenv:Body>'
        f"<pub:{operation}>{body}</pub:{operation}>"
        "</soapenv:Body></soapenv:Envelope>"
    )


def _text(item: ET.Element, name: str) -> str | None:
    el = item.find(f"{{{NS}}}{name}")
    return el.text if el is not None and el.text not in (None, "") else None


def parse_items(xml_text: str) -> list[dict[str, str]]:
    """Return every ``<Item>`` of an OTE response as a flat dict of child texts."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise FetchError(f"OTE returned invalid XML: {exc}") from exc
    fault = root.find(".//{http://schemas.xmlsoap.org/soap/envelope/}Fault")
    if fault is not None:
        msg = "".join(fault.itertext()).strip()
        raise FetchError(f"OTE SOAP fault: {msg}")
    items = []
    for item in root.iter(f"{{{NS}}}Item"):
        items.append({child.tag.split("}", 1)[1]: (child.text or "") for child in item})
    return items


def _chunks(days: list[date]) -> Iterable[tuple[date, date]]:
    """Group sorted days into contiguous runs within one calendar month.

    OTE rejects longer ranges ("Maximal range between start and end date is 1 month(s)"),
    e.g. 2025-02-01..2025-03-03 fails although it is only 31 days.
    """
    if not days:
        return
    start = prev = days[0]
    for d in days[1:]:
        if d - prev != timedelta(days=1) or (d.year, d.month) != (start.year, start.month):
            yield start, prev
            start = d
        prev = d
    yield start, prev


#: version of the per-month OTE cache document (``ote/YYYY-MM.json``)
CACHE_FORMAT = 2


def _quarter(day: date) -> bool:
    return day >= QUARTER_HOUR_MARKET_START


def expected_periods(day: date) -> int:
    """Exact number of OTE periods of a delivery day: local day length in hours
    (23/24/25 on DST days), ×4 in the 15-minute era (92/96/100)."""
    hours = (local_midnight_utc(day + timedelta(days=1)) - local_midnight_utc(day)) // HOUR
    return hours * 4 if _quarter(day) else hours


def _valid_rows(day: date, rows: object) -> bool:
    """Rows are complete: exactly the expected periods 1..n, with the regime's value keys."""
    n = expected_periods(day)
    if not isinstance(rows, list) or len(rows) != n:
        return False
    keys = ("eur", "eur_h") if _quarter(day) else ("czk",)
    for idx, r in enumerate(rows, start=1):
        if not isinstance(r, dict) or r.get("i") != idx or any(k not in r for k in keys):
            return False
    return True


def _day_entry(day: date, rows: list[dict]) -> dict:
    """Cached day: raw OTE values + their currency and resolution (no CZK conversion)."""
    if _quarter(day):
        return {"resolution": "PT15M", "currency": "EUR", "rows": rows}
    return {"resolution": "PT60M", "currency": "CZK", "rows": rows}


def _entry_rows(day: date, entry: object) -> list[dict] | None:
    if not isinstance(entry, dict):
        return None
    want = _day_entry(day, [])
    if entry.get("resolution") != want["resolution"] or entry.get("currency") != want["currency"]:
        return None
    rows = entry.get("rows")
    return rows if _valid_rows(day, rows) else None  # type: ignore[return-value]


@dataclass
class OteCacheStats:
    """What one run did: delivery days served from the cache / downloaded, OTE requests."""

    cached_days: int = 0
    downloaded_days: int = 0
    requests: int = 0
    #: downloaded days not stored (today/future or incomplete)
    not_cached_days: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


class OteClient:
    """Downloads and caches day-ahead prices.

    Cache: ``ote/YYYY-MM.json`` = ``{"format": 2, "days": {"YYYY-MM-DD": {"resolution",
    "currency", "rows"}}}``. Only complete past delivery days (exact period count, before
    the local ``today``) are stored; they are immutable, so they are never downloaded again.
    """

    def __init__(
        self,
        session: requests.Session,
        cache: JsonCache,
        cnb: CnbRates,
        today: date | None = None,
    ) -> None:
        self.session = session
        self.cache = cache
        self.cnb = cnb
        self.today = today or local_today()
        #: delivery day → number of periods without a market price (emergency state)
        self.unpriced: dict[date, int] = {}
        self.stats = OteCacheStats()
        self._months: dict[str, dict[str, dict]] = {}

    # -- raw access --------------------------------------------------------------------------
    def _call(self, operation: str, fields: dict[str, str]) -> list[dict[str, str]]:
        self.stats.requests += 1
        try:
            resp = self.session.post(
                OTE_SERVICE_URL,
                data=_envelope(operation, fields).encode("utf-8"),
                headers={
                    "Content-Type": "text/xml; charset=utf-8",
                    "SOAPAction": f'"{NS}/{operation}"',
                },
                timeout=DEFAULT_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise FetchError(f"OTE request {operation} failed: {exc}") from exc
        if resp.status_code != 200 and "Fault" not in resp.text:
            raise FetchError(f"OTE request {operation} failed: HTTP {resp.status_code}")
        return parse_items(resp.text)  # raises FetchError on SOAP faults (sent as HTTP 500)

    def _fetch_days(self, start: date, end: date) -> dict[str, list[dict]]:
        """Fetch [start, end] (same market regime, ≤31 days) → {iso day: normalised rows}."""
        out: dict[str, list[dict]] = {}
        if _quarter(start):
            items = self._call(
                "GetDamPricePeriodE",
                {
                    "StartDate": start.isoformat(),
                    "EndDate": end.isoformat(),
                    "PeriodResolution": "PT15M",
                },
            )
            for it in items:
                out.setdefault(it["Date"], []).append(
                    {
                        "i": int(it["PeriodIndex"]),
                        "eur": float(it["Price"]) if it.get("Price") else None,
                        "eur_h": float(it["HourlyPrice"]) if it.get("HourlyPrice") else None,
                    }
                )
        else:
            items = self._call(
                "GetDamPriceE",
                {"StartDate": start.isoformat(), "EndDate": end.isoformat(), "InEur": "false"},
            )
            for it in items:
                # periods without a price are marked <Emerg>1</Emerg> (market emergency state,
                # e.g. the 2025-07-04 blackout); keep them so the day counts as complete
                out.setdefault(it["Date"], []).append(
                    {"i": int(it["Hour"]), "czk": float(it["Price"]) if it.get("Price") else None}
                )
        for rows in out.values():
            rows.sort(key=lambda r: r["i"])
        return out

    # -- cache -------------------------------------------------------------------------------
    def _month(self, mk: str) -> dict[str, dict]:
        """Valid cached days of one month (legacy unversioned files are migrated)."""
        if mk in self._months:
            return self._months[mk]
        raw = self.cache.get("ote", mk)
        days: dict[str, dict] = {}
        dirty = False
        if isinstance(raw, dict) and raw.get("format") == CACHE_FORMAT:
            stored = raw.get("days")
            stored = stored if isinstance(stored, dict) else {}
        elif isinstance(raw, dict) and "format" not in raw:
            # format 1 (≤ 0.x): {"YYYY-MM-DD": rows}
            stored = {iso: rows for iso, rows in raw.items() if isinstance(rows, list)}
            dirty = True
        else:
            if raw is not None:
                self.cache.warn(
                    f"OTE cache file for {mk} has an unknown format; downloading the data again."
                )
            stored = {}
        for iso, entry in stored.items():
            try:
                d = date.fromisoformat(iso)
            except (TypeError, ValueError):
                dirty = True
                continue
            if isinstance(entry, list):  # legacy rows
                entry = _day_entry(d, entry)
            rows = _entry_rows(d, entry) if d < self.today else None
            if rows is None:
                dirty = True
                continue
            days[iso] = _day_entry(d, rows)
        self._months[mk] = days
        if dirty and raw is not None:
            self._save(mk)
        return days

    def _save(self, mk: str) -> None:
        self.cache.set("ote", mk, {"format": CACHE_FORMAT, "days": self._months[mk]})

    def _store(self, d: date, rows: list[dict]) -> bool:
        """Remember a downloaded day if it is a complete past day; True when stored."""
        if d >= self.today or not _valid_rows(d, rows):
            return False
        self._months.setdefault(f"{d.year:04d}-{d.month:02d}", {})[d.isoformat()] = _day_entry(
            d, rows
        )
        return True

    def _add_eur(self, by_day: dict[date, list[dict]]) -> None:
        """Add OTE's own EUR prices (``InEur=true``) to hourly-era rows that lack them."""
        need = sorted(
            d for d, rows in by_day.items() if not _quarter(d) and any("eur" not in r for r in rows)
        )
        dirty: set[str] = set()
        for a, b in _chunks(need):
            items = self._call(
                "GetDamPriceE",
                {"StartDate": a.isoformat(), "EndDate": b.isoformat(), "InEur": "true"},
            )
            eur: dict[tuple[str, int], float | None] = {
                (it["Date"], int(it["Hour"])): float(it["Price"]) if it.get("Price") else None
                for it in items
            }
            for d in iter_days(a, b):
                rows = by_day.get(d, [])
                for r in rows:
                    r["eur"] = eur.get((d.isoformat(), r["i"]))
                if self.cache.enabled and self._store(d, rows):
                    dirty.add(f"{d.year:04d}-{d.month:02d}")
        for mk in dirty:
            self._save(mk)

    def day_rows(self, days: list[date]) -> dict[date, list[dict]]:
        """Rows per day, from cache where possible. Days without published prices are omitted."""
        result: dict[date, list[dict]] = {}
        missing: list[date] = []
        for d in days:
            entry = self._month(f"{d.year:04d}-{d.month:02d}").get(d.isoformat())
            if entry is not None:
                result[d] = entry["rows"]
                self.stats.cached_days += 1
            else:
                missing.append(d)

        # never mix regimes in one request
        old = [d for d in missing if not _quarter(d)]
        new = [d for d in missing if _quarter(d)]
        dirty: set[str] = set()
        for group in (old, new):
            for a, b in _chunks(group):
                for iso, rows in self._fetch_days(a, b).items():
                    d = date.fromisoformat(iso)
                    if not a <= d <= b:
                        continue
                    result[d] = rows
                    self.stats.downloaded_days += 1
                    if self._store(d, rows):
                        dirty.add(iso[:7])
                    else:
                        self.stats.not_cached_days += 1
        for mk in dirty:
            self._save(mk)
        return result

    # -- public API -------------------------------------------------------------------------
    def prices(
        self,
        start: date,
        end_inclusive: date,
        resolution: PriceResolution = "auto",
        *,
        with_eur: bool = False,
    ) -> list[PricePoint]:
        """Price points covering local delivery days ``start``..``end_inclusive``.

        ``czk_mwh`` is always set. ``eur_mwh`` is set for the 15-minute era (OTE publishes
        EUR) and, with ``with_eur=True``, also for the hourly era (OTE's own EUR values).

        ``auto``   – 15-minute prices where the market had them (from 2025-10-01), else hourly.
        ``hourly`` – always hourly prices (OTE's ``HourlyPrice`` for the 15-minute era).
        """
        days = list(iter_days(start, end_inclusive))
        by_day = self.day_rows(days)
        if with_eur:
            self._add_eur(by_day)
        points: list[PricePoint] = []
        for d in days:
            rows = by_day.get(d)
            if not rows:
                continue
            missing = sum(1 for r in rows if r.get("czk", r.get("eur")) is None)
            if missing:
                self.unpriced[d] = missing
            if not _quarter(d):
                for r in rows:
                    if r["czk"] is not None:
                        start_utc = period_start_utc(d, r["i"], HOUR)
                        points.append(PricePoint(start_utc, HOUR, r["czk"], r.get("eur")))
                continue
            rate = self.cnb.eur_czk(d)
            if resolution == "hourly":
                seen: set[int] = set()
                for r in rows:
                    hour_idx = (r["i"] - 1) // 4 + 1
                    if hour_idx in seen or r.get("eur_h") is None:
                        continue
                    seen.add(hour_idx)
                    points.append(
                        PricePoint(
                            period_start_utc(d, hour_idx, HOUR),
                            HOUR,
                            r["eur_h"] * rate,
                            r["eur_h"],
                            rate,
                        )
                    )
            else:
                for r in rows:
                    if r["eur"] is None:
                        continue
                    points.append(
                        PricePoint(
                            period_start_utc(d, r["i"], QUARTER),
                            QUARTER,
                            r["eur"] * rate,
                            r["eur"],
                            rate,
                        )
                    )
        return points
