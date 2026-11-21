"""OTE-CR day-ahead market (DT/DAM) prices via OTE's public SOAP web service.

Service: https://www.ote-cr.cz/pw-data/services/PublicDataService (WSDL: ``?wsdl``)
Manual:  https://www.ote-cr.cz/cs/dokumentace/xsd-wsdl-manual-pubweb
         (``uzivatelsky-manual_webove_sluzby_ote_k.pdf``)

* Delivery days up to 2025-09-30: ``GetDamPriceE`` – hourly prices; ``InEur=false`` returns
  OTE's official CZK/MWh values directly.
* Delivery days from 2025-10-01 (15-minute day-ahead market): ``GetDamPricePeriodE`` with
  ``PeriodResolution=PT15M`` – 15-minute ``Price`` and the hourly ``HourlyPrice`` in EUR/MWh.
  Converted to CZK with the ČNB fixing for the delivery day (see :mod:`egd_api_client.spot.cnb`).

Requests are limited to one calendar month; periods are numbered from local
midnight (23/25 hours or 92/100 quarter-hours on DST days).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterable
from datetime import date, timedelta
from typing import Literal

import requests

from egd_api_client.cache import JsonCache
from egd_api_client.http import DEFAULT_TIMEOUT, FetchError
from egd_api_client.models import PricePoint
from egd_api_client.spot.cnb import CnbRates
from egd_api_client.timeutil import HOUR, QUARTER, iter_days, period_start_utc

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


def _complete(n_items: int, quarter: bool) -> bool:
    return n_items in ((92, 96, 100) if quarter else (23, 24, 25))


class OteClient:
    """Downloads and caches day-ahead prices. Cache key: one JSON file per month."""

    def __init__(self, session: requests.Session, cache: JsonCache, cnb: CnbRates) -> None:
        self.session = session
        self.cache = cache
        self.cnb = cnb
        #: delivery day → number of periods without a market price (emergency state)
        self.unpriced: dict[date, int] = {}

    # -- raw access --------------------------------------------------------------------------
    def _call(self, operation: str, fields: dict[str, str]) -> list[dict[str, str]]:
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
        if start >= QUARTER_HOUR_MARKET_START:
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

    def day_rows(self, days: list[date]) -> dict[date, list[dict]]:
        """Rows per day, from cache where possible. Days without published prices are omitted."""
        result: dict[date, list[dict]] = {}
        missing: list[date] = []
        months: dict[str, dict] = {}
        for d in days:
            mk = f"{d.year:04d}-{d.month:02d}"
            if mk not in months:
                months[mk] = self.cache.get("ote", mk) or {}
            rows = months[mk].get(d.isoformat())
            if rows:
                result[d] = rows
            else:
                missing.append(d)

        # never mix regimes in one request
        old = [d for d in missing if d < QUARTER_HOUR_MARKET_START]
        new = [d for d in missing if d >= QUARTER_HOUR_MARKET_START]
        dirty: set[str] = set()
        for group in (old, new):
            for a, b in _chunks(group):
                for iso, rows in self._fetch_days(a, b).items():
                    d = date.fromisoformat(iso)
                    result[d] = rows
                    if _complete(len(rows), d >= QUARTER_HOUR_MARKET_START):
                        mk = iso[:7]
                        months.setdefault(mk, {})[iso] = rows
                        dirty.add(mk)
        for mk in dirty:
            self.cache.set("ote", mk, months[mk])
        return result

    # -- public API -------------------------------------------------------------------------
    def prices(
        self, start: date, end_inclusive: date, resolution: PriceResolution = "auto"
    ) -> list[PricePoint]:
        """Price points (CZK/MWh) covering local delivery days ``start``..``end_inclusive``.

        ``auto``   – 15-minute prices where the market had them (from 2025-10-01), else hourly.
        ``hourly`` – always hourly prices (OTE's ``HourlyPrice`` for the 15-minute era).
        """
        days = list(iter_days(start, end_inclusive))
        by_day = self.day_rows(days)
        points: list[PricePoint] = []
        for d in days:
            rows = by_day.get(d)
            if not rows:
                continue
            missing = sum(1 for r in rows if r.get("czk", r.get("eur")) is None)
            if missing:
                self.unpriced[d] = missing
            if d < QUARTER_HOUR_MARKET_START:
                for r in rows:
                    if r["czk"] is not None:
                        points.append(PricePoint(period_start_utc(d, r["i"], HOUR), HOUR, r["czk"]))
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
