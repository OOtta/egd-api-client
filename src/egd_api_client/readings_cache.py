"""Persistent per-day cache of EG.D metering data (15-minute readings).

Files: ``<cache dir>/egd/<id>_<variant>-<profile>-<timestamps>_<YYYY-MM>.json`` where ``<id>``
is an HMAC-SHA256 of the EAN keyed with a random secret stored next to the files
(``egd/.key``), so the EAN never appears in a file name and cannot be recovered from it by
trying all EANs. Directories are created ``0700`` and files ``0600`` (POSIX).

Only *final* days are stored: complete (exactly the day's number of quarter-hours, 92/96/100
on DST days), all values with a final status, and older than ``final_after_days`` (EG.D may
still revise recent data). Today and future days are never stored. A per-metering-point meta
file remembers the resolved default profile and the first day the account has access to, so a
fully cached run sends no request at all (not even for the token).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from egd_api_client.cache import JsonCache
from egd_api_client.client import C1_LEGACY_PROFILE, VALID_STATUSES, EgdClient
from egd_api_client.models import LoadResult, Reading
from egd_api_client.timeutil import (
    QUARTER,
    iter_days,
    local_date,
    local_midnight_utc,
    local_today,
)

NAMESPACE = "egd"
CACHE_FORMAT = 1
DEFAULT_FINAL_AFTER_DAYS = 7


def quarter_hours(day: date) -> int:
    """Number of 15-minute intervals of a local day (92/96/100)."""
    return (local_midnight_utc(day + timedelta(days=1)) - local_midnight_utc(day)) // QUARTER


@dataclass
class ReadingsCacheStats:
    cached_days: int = 0
    downloaded_days: int = 0
    requests: int = 0
    #: downloaded days not stored (today/future, recent, incomplete or non-final values)
    not_cached_days: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def _secret(cache: JsonCache) -> bytes:
    assert cache.directory is not None
    path = cache.directory / NAMESPACE / ".key"
    try:
        return bytes.fromhex(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        pass
    cache.private_dir(path.parent)
    key = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(key.hex())
    return key


def ean_id(cache: JsonCache, ean: str) -> str:
    return hmac.new(_secret(cache), ean.encode(), hashlib.sha256).hexdigest()[:20]


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "", s)


def _ranges(days: list[date]) -> list[tuple[date, date]]:
    out: list[tuple[date, date]] = []
    for d in days:
        if out and d - out[-1][1] == timedelta(days=1):
            out[-1] = (out[-1][0], d)
        else:
            out.append((d, d))
    return out


class CachedReadings:
    """Day-level cache in front of :meth:`EgdClient.readings`."""

    def __init__(
        self,
        cache: JsonCache,
        client_factory: Callable[[], EgdClient],
        *,
        today: date | None = None,
        final_after_days: int = DEFAULT_FINAL_AFTER_DAYS,
    ) -> None:
        self.cache = cache
        self._factory = client_factory
        self._client: EgdClient | None = None
        self.today = today or local_today()
        self.final_after_days = max(0, final_after_days)
        self.stats = ReadingsCacheStats()

    @property
    def client(self) -> EgdClient:
        if self._client is None:
            self._client = self._factory()
        return self._client

    def _final(self, day: date, readings: list[Reading]) -> bool:
        if day >= self.today - timedelta(days=self.final_after_days) or day >= self.today:
            return False
        n = quarter_hours(day)
        if len(readings) != n:
            return False
        t0 = local_midnight_utc(day)
        return all(
            r.start == t0 + i * QUARTER
            and r.length == QUARTER
            and (r.status is None or r.status in VALID_STATUSES)
            for i, r in enumerate(readings)
        )

    def readings(
        self,
        ean: str,
        start: date,
        end: date,
        *,
        profile: str | None,
        variant: str,
        timestamps: str | None,
        kind: str,
        test_env: bool,
    ) -> LoadResult:
        """Readings for local days ``start``..``end`` (inclusive)."""
        fetch_kw = {"variant": variant, "timestamps": timestamps, "kind": kind}
        if not self.cache.enabled:
            t0, t1 = local_midnight_utc(start), local_midnight_utc(end + timedelta(days=1))
            res = self.client.readings(ean, t0, t1, profile=profile, today=self.today, **fetch_kw)
            self.stats.requests = self.client.requests
            self.stats.downloaded_days = len({local_date(r.start) for r in res.readings})
            return res

        eid = ean_id(self.cache, ean)
        meta_key = f"{eid}_meta"
        meta = self.cache.get(NAMESPACE, meta_key)
        if not isinstance(meta, dict) or meta.get("format") != CACHE_FORMAT:
            meta = {"format": CACHE_FORMAT, "ean": "…" + ean[-4:], "profiles": {}, "access": {}}
        meta_dirty = False

        if variant == "c1-legacy":
            profile = profile or C1_LEGACY_PROFILE
        elif profile is None:
            pkey = f"{kind}{'-test' if test_env else ''}"
            profile = meta["profiles"].get(pkey)
            if not profile:
                profile = self.client.default_profile(ean, kind)  # type: ignore[arg-type]
                meta["profiles"][pkey] = profile
                meta_dirty = True
        ts = timestamps or ("end" if variant == "c1-legacy" else "start")
        tag = _safe(f"{variant}-{profile}-{ts}{'-test' if test_env else ''}")
        akey = tag

        warnings: list[str] = []
        available_from = None
        access = meta["access"].get(akey)
        days = list(iter_days(start, end))
        if access and start < date.fromisoformat(access):
            first = date.fromisoformat(access)
            days = [d for d in days if d >= first]
            available_from = local_midnight_utc(first)
            warnings.append(
                f"Your EG.D access covers this metering point only since {first}; "
                "earlier data were skipped."
            )

        months: dict[str, dict] = {}

        def month(d: date) -> dict:
            mk = f"{d.year:04d}-{d.month:02d}"
            if mk not in months:
                doc = self.cache.get(NAMESPACE, f"{eid}_{tag}_{mk}")
                ok = isinstance(doc, dict) and doc.get("format") == CACHE_FORMAT
                if doc is not None and not ok:
                    self.cache.warn(f"EG.D data cache for {mk} is invalid; downloading again.")
                stored = doc.get("days") if ok else None  # type: ignore[union-attr]
                months[mk] = {"days": stored if isinstance(stored, dict) else {}}
            return months[mk]

        out: list[Reading] = []
        missing: list[date] = []
        for d in days:
            rows = month(d)["days"].get(d.isoformat()) if d < self.today else None
            got = self._decode(d, rows)
            if got is not None:
                out.extend(got)
                self.stats.cached_days += 1
            else:
                missing.append(d)

        dirty: set[str] = set()
        for a, b in _ranges(missing):
            t0, t1 = local_midnight_utc(a), local_midnight_utc(b + timedelta(days=1))
            res = self.client.readings(ean, t0, t1, profile=profile, today=self.today, **fetch_kw)
            for w in res.warnings:
                if w not in warnings:
                    warnings.append(w)
            if res.available_from is not None:
                available_from = available_from or res.available_from
                first = local_date(res.available_from)
                if meta["access"].get(akey) != first.isoformat():
                    meta["access"][akey] = first.isoformat()
                    meta_dirty = True
            by_day: dict[date, list[Reading]] = {}
            for r in res.readings:
                by_day.setdefault(local_date(r.start), []).append(r)
            for d, rs in sorted(by_day.items()):
                out.extend(rs)
                self.stats.downloaded_days += 1
                if self._final(d, rs):
                    month(d)["days"][d.isoformat()] = [
                        [r.kwh, r.status] if r.status else [r.kwh] for r in rs
                    ]
                    dirty.add(f"{d.year:04d}-{d.month:02d}")
                else:
                    self.stats.not_cached_days += 1
        for mk in dirty:
            self.cache.set(
                NAMESPACE,
                f"{eid}_{tag}_{mk}",
                {"format": CACHE_FORMAT, "profile": profile, "days": months[mk]["days"]},
            )
        if meta_dirty:
            self.cache.set(NAMESPACE, meta_key, meta)
        if self._client is not None:
            self.stats.requests = self._client.requests
        out.sort(key=lambda r: r.start)
        return LoadResult(out, warnings, available_from)

    def _decode(self, day: date, rows: object) -> list[Reading] | None:
        if not isinstance(rows, list) or len(rows) != quarter_hours(day):
            return None
        t0 = local_midnight_utc(day)
        out = []
        for i, row in enumerate(rows):
            if not isinstance(row, list) or not 1 <= len(row) <= 2:
                return None
            kwh = row[0]
            if not isinstance(kwh, int | float) or isinstance(kwh, bool):
                return None
            status = row[1] if len(row) == 2 else None
            out.append(Reading(t0 + i * QUARTER, float(kwh), QUARTER, status))
        return out
