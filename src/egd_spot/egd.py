"""Client for the EG.D (E.ON Distribuce) "Vzdálený přístup / OpenAPI" measured-data interface.

Documentation (Czech PDF, "Uživatelský návod – vzdálený přístup/OpenApi"):

* https://www.egd.cz/sites/default/files/2026-05/uzivatelsky_navod_openapi_abc.pdf (latest)
* https://www.egd.cz/sites/default/files/2025-12/uzivatelsky_navod_openapi_abc_0.pdf
* https://portal.distribuce24.cz/download/Uzivatelsky_navod_OpenApi.pdf (original A/B version)

Summary of the documented interface:

* ``POST https://idm.distribuce24.cz/oauth/token`` with JSON
  ``{"grant_type": "client_credentials", "client_id": ..., "client_secret": ...,
  "scope": "namerena_data_openapi"}`` → ``{"access_token": ..., "token_type": "bearer"}``.
  The token is valid until midnight of the day it was issued.
* ``GET https://data.distribuce24.cz/rest/spotreby?ean=…&profile=ICC1&from=…&to=…``
  (``Authorization: Bearer <token>``; times in UTC, ``YYYY-MM-DDTHH:MM:00.000Z``; max 3000
  records per call, optional ``pageStart`` (≥1) / ``pageSize`` (≤3000)).
* ``GET https://data.distribuce24.cz/rest/om`` – metering points available to the credentials.
* Profiles: ``ICC1`` consumption (average kW per 15 min → divide by 4 for kWh),
  ``ICQ2`` consumption energy in kWh (since 2024-07-01), ``ISC1``/``ISQ2`` export to grid.
* A test environment exists at ``https://test.distribuce24.cz`` (``/idm/oauth/token``,
  ``/openApi/spotreby`` …).
* The 2025-12 revision documented a separate endpoint for smart meters (measurement type C1):
  ``/rest/c/spotreby`` with OBIS-like profile codes and ``zdrojDat``. The 2026-05 revision
  lists A, B and C1 under the unified ``/rest/spotreby``. Both are supported here
  (``api_variant="rest"`` – default – or ``"c1-legacy"``).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

import requests

from egd_spot.http import DEFAULT_TIMEOUT, FetchError
from egd_spot.models import LoadResult, Reading
from egd_spot.timeutil import QUARTER, format_api_utc, parse_iso_utc

PROD_TOKEN_URL = "https://idm.distribuce24.cz/oauth/token"
PROD_DATA_URL = "https://data.distribuce24.cz/rest"
TEST_TOKEN_URL = "https://test.distribuce24.cz/idm/oauth/token"
TEST_DATA_URL = "https://test.distribuce24.cz/openApi"
SCOPE = "namerena_data_openapi"

DEFAULT_PROFILE = "ICC1"
C1_LEGACY_PROFILE = "0.0.2.4.1.2.12.0.0.0.0.0.0.0.0.3.72.0"  # "Spotřeba energie celkem (kWh)"
POWER_PROFILES = frozenset({"ICC1", "ISC1", "IKC1", "IMC1"})  # mean kW / kVAr per 15 min
VALID_STATUSES = frozenset({"IU012", "W"})
PAGE_SIZE = 3000
CHUNK = timedelta(days=30)  # 30 × 96 (+4 on DST) < 3000 records

ApiVariant = Literal["rest", "c1-legacy"]
TimestampConvention = Literal["start", "end"]


class EgdAuthError(FetchError):
    pass


@dataclass(slots=True)
class EgdCredentials:
    client_id: str
    client_secret: str

    def __repr__(self) -> str:  # never leak the secret into logs/tracebacks
        return f"EgdCredentials(client_id={self.client_id!r}, client_secret='***')"


class EgdClient:
    def __init__(
        self,
        session: requests.Session,
        credentials: EgdCredentials,
        *,
        test_env: bool = False,
        token_url: str | None = None,
        data_url: str | None = None,
    ) -> None:
        self.session = session
        self.credentials = credentials
        self.token_url = token_url or (TEST_TOKEN_URL if test_env else PROD_TOKEN_URL)
        self.data_url = (data_url or (TEST_DATA_URL if test_env else PROD_DATA_URL)).rstrip("/")
        self._token: str | None = None

    # -- auth -------------------------------------------------------------------------------
    def token(self) -> str:
        if self._token:
            return self._token
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.credentials.client_id,
            "client_secret": self.credentials.client_secret,
            "scope": SCOPE,
        }
        try:
            resp = self.session.post(self.token_url, json=payload, timeout=DEFAULT_TIMEOUT)
        except requests.RequestException as exc:
            raise EgdAuthError(f"Cannot reach EG.D token endpoint: {exc}") from exc
        if resp.status_code != 200:
            raise EgdAuthError(
                f"EG.D token request failed (HTTP {resp.status_code}). "
                "Check client_id/client_secret (generated in portal.distribuce24.cz → "
                "Správa účtů → Vzdálený přístup – OpenAPI)."
            )
        try:
            token = resp.json()["access_token"]
        except (ValueError, KeyError) as exc:
            raise EgdAuthError("EG.D token response has no access_token") from exc
        self._token = str(token)
        return self._token

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.data_url}/{path.lstrip('/')}"
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self.token()}", "Accept": "application/json"}
            try:
                resp = self.session.get(
                    url, params=params, headers=headers, timeout=DEFAULT_TIMEOUT
                )
            except requests.RequestException as exc:
                raise FetchError(f"EG.D request to {url} failed: {exc}") from exc
            if resp.status_code == 401 and attempt == 1:
                self._token = None  # token expired at midnight – get a fresh one
                continue
            if resp.status_code != 200:
                raise FetchError(f"EG.D {path} returned HTTP {resp.status_code}: {resp.text[:300]}")
            try:
                return resp.json()
            except ValueError as exc:
                raise FetchError(f"EG.D {path} returned non-JSON data") from exc
        raise FetchError("unreachable")  # pragma: no cover

    # -- endpoints --------------------------------------------------------------------------
    def metering_points(self) -> list[dict[str, Any]]:
        data = self._get("om")
        return data if isinstance(data, list) else [data]

    def profiles(self, variant: ApiVariant = "rest") -> list[dict[str, Any]]:
        return self._get("c/profily" if variant == "c1-legacy" else "profily")

    def readings(
        self,
        ean: str,
        start: datetime,
        end: datetime,
        *,
        profile: str | None = None,
        variant: ApiVariant = "rest",
        data_source: str = "ODBERNE_MISTO",
        timestamps: TimestampConvention | None = None,
    ) -> LoadResult:
        """Download readings for the UTC half-open range [start, end)."""
        if variant == "c1-legacy":
            profile = profile or C1_LEGACY_PROFILE
            ts_conv: TimestampConvention = timestamps or "end"
        else:
            profile = profile or DEFAULT_PROFILE
            ts_conv = timestamps or "start"

        readings: dict[datetime, Reading] = {}
        bad_status: dict[str, int] = {}
        for chunk_start, chunk_end in _chunks(start, end):
            # API wants an inclusive range of interval timestamps
            if ts_conv == "start":
                q_from, q_to = chunk_start, chunk_end - QUARTER
            else:
                q_from, q_to = chunk_start + QUARTER, chunk_end
            for raw in self._paged(ean, profile, q_from, q_to, variant, data_source):
                for r in parse_readings(raw, ts_conv):
                    if start <= r.start < end:
                        readings[r.start] = r
                        if r.status and r.status not in VALID_STATUSES:
                            bad_status[r.status] = bad_status.get(r.status, 0) + 1
        warnings = []
        if bad_status:
            detail = ", ".join(f"{k}×{v}" for k, v in sorted(bad_status.items()))
            warnings.append(
                f"Some EG.D values are not final/valid (status {detail}); they were included. "
                "EG.D recommends re-downloading such data later."
            )
        return LoadResult(sorted(readings.values(), key=lambda r: r.start), warnings)

    def _paged(
        self,
        ean: str,
        profile: str,
        q_from: datetime,
        q_to: datetime,
        variant: ApiVariant,
        data_source: str,
    ) -> Iterator[Any]:
        params: dict[str, Any] = {
            "ean": ean,
            "profile": profile,
            "from": format_api_utc(q_from),
            "to": format_api_utc(q_to),
        }
        if variant == "c1-legacy":
            params["zdrojDat"] = data_source
            yield self._get("c/spotreby", params)
            return
        page_start = 1
        seen_first: set[str] = set()
        while True:
            page = self._get("spotreby", {**params, "pageStart": page_start, "pageSize": PAGE_SIZE})
            yield page
            n = _count_records(page)
            first = _first_timestamp(page)
            if n < PAGE_SIZE or first is None or first in seen_first:
                return
            seen_first.add(first)
            page_start += PAGE_SIZE


def _chunks(start: datetime, end: datetime) -> Iterator[tuple[datetime, datetime]]:
    cur = start
    while cur < end:
        nxt = min(cur + CHUNK, end)
        yield cur, nxt
        cur = nxt


def _blocks(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [b for b in payload if isinstance(b, dict)]
    return []


def _count_records(payload: Any) -> int:
    return sum(len(b.get("data") or []) for b in _blocks(payload))


def _first_timestamp(payload: Any) -> str | None:
    for b in _blocks(payload):
        for rec in b.get("data") or []:
            return str(rec.get("timestamp"))
    return None


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, str):
        v = v.replace(",", ".")
    return float(v)


def parse_readings(payload: Any, timestamps: TimestampConvention = "start") -> list[Reading]:
    """Parse a ``/spotreby`` (or legacy ``/c/spotreby``) response into kWh readings.

    Power profiles (``units: KW``) are mean 15-minute power and are converted to kWh (÷4).
    """
    out: list[Reading] = []
    for block in _blocks(payload):
        if "MeasurementsValues" in block:  # legacy C1 shape (values already in kWh)
            for meter in block.get("MeasurementsValues") or []:
                for rec in meter.get("Measurements") or []:
                    val = _to_float(rec.get("value"))
                    if val is None:
                        continue
                    ts = parse_iso_utc(rec["datetime"])
                    start = ts - QUARTER if timestamps == "end" else ts
                    out.append(Reading(start, val, QUARTER, rec.get("status")))
            continue
        units = str(block.get("units") or "").upper()
        if units:
            factor = 0.25 if units in ("KW", "KVAR") else 1.0
        else:  # fall back to the documented unit of the profile
            factor = 0.25 if str(block.get("profile", "")).upper() in POWER_PROFILES else 1.0
        for rec in block.get("data") or []:
            val = _to_float(rec.get("value"))
            if val is None:
                continue
            ts = parse_iso_utc(str(rec["timestamp"]))
            start = ts - QUARTER if timestamps == "end" else ts
            out.append(Reading(start, val * factor, QUARTER, rec.get("status")))
    return out
