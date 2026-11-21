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
  records per call, optional ``pageStart`` / ``pageSize`` – not used, see below).
* ``GET https://data.distribuce24.cz/rest/om`` – metering points available to the credentials.
* Profiles: ``ICC1`` consumption (average kW per 15 min → divide by 4 for kWh),
  ``ICQ2`` consumption energy in kWh (since 2024-07-01), ``ISC1``/``ISQ2`` export to grid.
* A test environment exists at ``https://test.distribuce24.cz`` (``/idm/oauth/token``,
  ``/openApi/spotreby`` …).
* The 2025-12 revision documented a separate endpoint for smart meters (measurement type C1):
  ``/rest/c/spotreby`` with OBIS-like profile codes and ``zdrojDat``. The 2026-05 revision
  lists A, B and C1 under the unified ``/rest/spotreby``. Both are supported here
  (``api_variant="rest"`` – default – or ``"c1-legacy"``).

Behaviour observed against the live API (Nov 2026, measurement type B):

* responses are a single JSON object ``{"ean/eic", "profile", "units": "kW"|"kWh",
  "total", "data": [{"timestamp": "…Z", "value": 0.12, "status": "W"}]}``;
* timestamps mark the interval **start**; ``from`` is inclusive and ``to`` exclusive;
* ``to`` must lie before today's local midnight (HTTP 400 "…nejvýše ke včerejšímu dni");
  ``from`` at most 3 years back; outside the customer's authorised period HTTP 400
  "V požadovaném období nemáte oprávnění…" for the whole request;
* a 31-day request returns all ~2980 values at once; ``pageStart=1`` (as documented) drops
  the first value of the range, so paging parameters are not sent at all;
* ``/rest/profily`` lists ``ICC1``/``ICQ2`` for types A/B and ``DCQC`` for C1.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

import requests

from egd_api_client.http import DEFAULT_TIMEOUT, FetchError
from egd_api_client.models import LoadResult, Reading
from egd_api_client.timeutil import (
    QUARTER,
    format_api_utc,
    local_date,
    local_midnight_utc,
    parse_iso_utc,
)

PROD_TOKEN_URL = "https://idm.distribuce24.cz/oauth/token"
PROD_DATA_URL = "https://data.distribuce24.cz/rest"
TEST_TOKEN_URL = "https://test.distribuce24.cz/idm/oauth/token"
TEST_DATA_URL = "https://test.distribuce24.cz/openApi"
SCOPE = "namerena_data_openapi"

DEFAULT_PROFILE = "ICC1"  # measurement types A, B
C1_PROFILE = "DCQC"  # measurement type C1 (smart meter): "Spotřeba energie odebrané ze sítě"
PROFILES = {
    (False, "consumption"): DEFAULT_PROFILE,
    (False, "production"): "ISC1",  # export to the grid (přetok), mean kW
    (True, "consumption"): C1_PROFILE,
    (True, "production"): "DSQC",  # "Energie dodávky do sítě (přetok) - C1", kWh
}
MAX_PAGES = 10
TRUNCATION_THRESHOLD = 2900  # a response this long may have hit the 3000-value cap
C1_LEGACY_PROFILE = "0.0.2.4.1.2.12.0.0.0.0.0.0.0.0.3.72.0"  # "Spotřeba energie celkem (kWh)"
POWER_PROFILES = frozenset({"ICC1", "ISC1", "IKC1", "IKC2", "IMC1"})  # mean kW / kVAr per 15 min
# final values per /rest/statusy: W valid; B, E, M, N substitute but final ("náhradní platná");
# G, V are temporary substitutes, F missing. IU012 = valid in the older A/B code list.
VALID_STATUSES = frozenset({"W", "B", "E", "M", "N", "IU012"})
CHUNK = timedelta(days=30)  # 30 × 96 (+4 on DST) < 3000 records

ApiVariant = Literal["rest", "c1-legacy"]
TimestampConvention = Literal["start", "end"]
Kind = Literal["consumption", "production"]


class EgdAuthError(FetchError):
    pass


class EgdRequestError(FetchError):
    """Non-200 answer from the data API; EG.D returns ``{"error", "message"}`` (Czech)."""

    def __init__(self, message: str, api_message: str = "", status: int = 0) -> None:
        super().__init__(message)
        self.api_message = api_message
        self.status = status

    @property
    def no_access(self) -> bool:
        # "V požadovaném období nemáte oprávnění na data odběrného místa."
        return "oprávnění" in self.api_message

    @classmethod
    def from_response(cls, path: str, resp: requests.Response) -> EgdRequestError:
        api_message = ""
        try:
            body = resp.json()
            if isinstance(body, dict):
                api_message = str(body.get("message") or body.get("error") or "")
        except ValueError:
            api_message = resp.text[:300]
        hint = ""
        if "včerejšímu" in api_message:
            hint = " (data are available only up to yesterday)"
        elif "3 roky" in api_message:
            hint = " (data are available for at most the last 3 years)"
        elif "oprávnění" in api_message:
            hint = " (your account has no access to this metering point for that period)"
        return cls(
            f"EG.D {path} returned HTTP {resp.status_code}: {api_message}{hint}",
            api_message,
            resp.status_code,
        )


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
                raise EgdRequestError.from_response(path, resp)
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

    def default_profile(self, ean: str, kind: Kind = "consumption") -> str:
        """Profile matching the metering point's measurement type.

        ``/rest/profily`` lists ``ICC1``/``ISC1`` (power) and ``ICQ2``/``ISQ2`` (energy) for
        types A and B and ``DCQC``/``DSQC`` for C1 smart meters; requesting a profile of the
        wrong type returns an empty data set.
        """
        c1 = False
        try:
            for p in self.metering_points():
                if str(p.get("ean")) == str(ean):
                    c1 = str(p.get("typMereni", "")).upper().startswith("C")
        except FetchError:
            pass
        return PROFILES[(c1, kind)]

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
        today: date | None = None,
        kind: Kind = "consumption",
    ) -> LoadResult:
        """Download readings for the UTC half-open range [start, end).

        * EG.D serves data only up to yesterday (local time); later parts are dropped.
        * If the account has no access to the beginning of the range (data are available only
          since the customer's contract/authorisation began), the first accessible day is
          located and the earlier part is skipped with a warning.
        """
        warnings: list[str] = []
        limit = local_midnight_utc(today or date.today())
        if end > limit:
            end = limit
            warnings.append(
                "EG.D provides data only up to yesterday; the period was truncated to "
                f"{local_date(limit - QUARTER)}."
            )
        if variant == "c1-legacy":
            profile = profile or C1_LEGACY_PROFILE
            ts_conv: TimestampConvention = timestamps or "end"
        else:
            profile = profile or self.default_profile(ean, kind)
            ts_conv = timestamps or "start"

        readings: dict[datetime, Reading] = {}
        bad_status: dict[str, int] = {}
        first_access: datetime | None = None
        for chunk_start, chunk_end in _chunks(start, end):
            try:
                pages = list(
                    self._fetch_chunk(
                        ean, profile, chunk_start, chunk_end, variant, data_source, ts_conv
                    )
                )
            except EgdRequestError as exc:
                if not exc.no_access or readings:
                    raise
                found = self._first_accessible(ean, profile, chunk_start, chunk_end)
                if found is None:
                    continue  # nothing accessible in this chunk – try the next one
                first_access = found
                pages = list(
                    self._fetch_chunk(ean, profile, found, chunk_end, variant, data_source, ts_conv)
                )
            for raw in pages:
                for r in parse_readings(raw, ts_conv):
                    if start <= r.start < end:
                        readings[r.start] = r
                        if r.status and r.status not in VALID_STATUSES:
                            bad_status[r.status] = bad_status.get(r.status, 0) + 1
        if first_access is not None:
            warnings.append(
                f"Your EG.D access covers this metering point only since "
                f"{local_date(first_access)}; earlier data were skipped."
            )
        elif not readings and start < end:
            warnings.append(
                f"EG.D returned no data for this period (profile {profile}; no access yet, "
                "or try another --profile)."
            )
        if bad_status:
            detail = ", ".join(f"{k}×{v}" for k, v in sorted(bad_status.items()))
            warnings.append(
                f"Some EG.D values are not final/valid (status {detail}); they were included. "
                "EG.D recommends re-downloading such data later."
            )
        return LoadResult(sorted(readings.values(), key=lambda r: r.start), warnings, first_access)

    def _first_accessible(
        self, ean: str, profile: str, chunk_start: datetime, chunk_end: datetime
    ) -> datetime | None:
        """Binary search for the first local midnight from which [midnight, chunk_end) is
        accessible. Returns None when nothing in the chunk is accessible."""
        days = []
        d = local_date(chunk_start)
        while local_midnight_utc(d) < chunk_end:
            m = local_midnight_utc(d)
            days.append(max(m, chunk_start))
            d += timedelta(days=1)

        def ok(t: datetime) -> bool:
            try:
                self._get(
                    "spotreby",
                    {
                        "ean": ean,
                        "profile": profile,
                        "from": format_api_utc(t),
                        "to": _format_to(min(t + QUARTER * 4, chunk_end)),
                    },
                )
            except EgdRequestError as exc:
                if exc.no_access:
                    return False
                raise
            return True

        lo, hi = 0, len(days)  # invariant: days[hi:] accessible, days[:lo] not
        while lo < hi:
            mid = (lo + hi) // 2
            if ok(days[mid]):
                hi = mid
            else:
                lo = mid + 1
        return days[lo] if lo < len(days) else None

    def _fetch_chunk(
        self,
        ean: str,
        profile: str,
        chunk_start: datetime,
        chunk_end: datetime,
        variant: ApiVariant,
        data_source: str,
        ts_conv: TimestampConvention,
    ) -> Iterator[Any]:
        if variant == "c1-legacy":
            # legacy endpoint: inclusive range of interval-end timestamps
            q_from, q_to = (
                (chunk_start + QUARTER, chunk_end)
                if ts_conv == "end"
                else (chunk_start, chunk_end - QUARTER)
            )
            params = {
                "ean": ean,
                "profile": profile,
                "from": format_api_utc(q_from),
                "to": format_api_utc(q_to),
                "zdrojDat": data_source,
            }
            yield self._get("c/spotreby", params)
            return
        # Observed behaviour of /rest/spotreby: "to" is exclusive, timestamps mark interval
        # starts, and "to" may not reach today's local midnight – so ask for ...:59.999Z.
        # pageStart/pageSize are NOT used: with pageStart=1 the live API silently drops the
        # first value (the guide's own example uses PageStart=0). A 30-day chunk (≤ 2884
        # values) fits into one response; should a response ever be truncated, continue
        # from the last timestamp received.
        q_from = chunk_start
        for _ in range(MAX_PAGES):
            page = self._get(
                "spotreby",
                {
                    "ean": ean,
                    "profile": profile,
                    "from": format_api_utc(q_from),
                    "to": _format_to(chunk_end),
                },
            )
            yield page
            n = _count_records(page)
            last = _last_timestamp(page)
            if n < TRUNCATION_THRESHOLD or last is None:
                return
            nxt = parse_iso_utc(last) + QUARTER
            if nxt >= chunk_end or nxt <= q_from:
                return
            q_from = nxt


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


def _last_timestamp(payload: Any) -> str | None:
    last = None
    for b in _blocks(payload):
        for rec in b.get("data") or []:
            last = str(rec.get("timestamp"))
    return last


def _format_to(end_exclusive: datetime) -> str:
    """Exclusive range end as EG.D accepts it (one millisecond before ``end_exclusive``)."""
    return (end_exclusive - timedelta(minutes=1)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:59.999Z")


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
