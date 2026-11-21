"""Helpers shared by all CLI commands (core and feature subpackages)."""

from __future__ import annotations

import argparse
import calendar
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from egd_api_client.cache import JsonCache, default_cache_dir
from egd_api_client.client import EgdClient, EgdCredentials, Kind
from egd_api_client.config import Config, load_config
from egd_api_client.http import make_session
from egd_api_client.models import LoadResult
from egd_api_client.timeutil import local_date, local_day_bounds_utc


class CliError(Exception):
    """User-facing error; printed without a traceback."""


def warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


def info(msg: str, quiet: bool = False) -> None:
    if not quiet:
        print(msg, file=sys.stderr)


def first(*values: Any) -> Any:
    return next((v for v in values if v is not None), None)


# ------------------------------------------------------------------------------------------
# dates
# ------------------------------------------------------------------------------------------
def parse_date_arg(value: str, *, end: bool = False) -> date:
    """``YYYY-MM-DD`` or ``YYYY-MM`` (first / last day of the month for --from / --to)."""
    try:
        if len(value) == 7:
            y, m = (int(x) for x in value.split("-"))
            return date(y, m, calendar.monthrange(y, m)[1] if end else 1)
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r} (use YYYY-MM-DD or YYYY-MM)"
        ) from exc


def resolve_period(args: argparse.Namespace, data: LoadResult | None = None) -> tuple[date, date]:
    start = parse_date_arg(args.date_from) if args.date_from else None
    if args.date_to:
        end = parse_date_arg(args.date_to, end=True)
    elif args.date_from and len(args.date_from) == 7:
        end = parse_date_arg(args.date_from, end=True)
    elif args.date_from:
        end = date.today() - timedelta(days=1)
    else:
        end = None
    if data is not None and data.readings:
        start = start or local_date(data.readings[0].start)
        end = end or local_date(data.readings[-1].start)
    if start is None or end is None:
        raise CliError("--from is required (and --to, unless --from is a month YYYY-MM)")
    if end < start:
        raise CliError(f"--to ({end}) is before --from ({start})")
    return start, end


def clamp_to_yesterday(start: date, end: date) -> tuple[date, date]:
    """EG.D serves data only up to yesterday (local time)."""
    yesterday = date.today() - timedelta(days=1)
    if end > yesterday:
        end = yesterday
        warn(f"EG.D provides data only up to yesterday; using --to {end}.")
    if end < start:
        raise CliError("No EG.D data can exist for this period yet.")
    return start, end


# ------------------------------------------------------------------------------------------
# shared argument groups
# ------------------------------------------------------------------------------------------
def common_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    g = p.add_argument_group("general")
    g.add_argument("--config", metavar="FILE", help="config file (TOML); env EGD_CONFIG")
    g.add_argument(
        "--cache-dir", metavar="DIR", help="cache directory (default ~/.cache/egd-api-client)"
    )
    g.add_argument("--no-cache", action="store_true", help="do not read or write the cache")
    g.add_argument("--json", action="store_true", help="print machine-readable JSON to stdout")
    return p


def period_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    g = p.add_argument_group("period (local Czech dates, inclusive)")
    g.add_argument("--from", dest="date_from", metavar="DATE", help="YYYY-MM-DD or YYYY-MM")
    g.add_argument("--to", dest="date_to", metavar="DATE", help="YYYY-MM-DD or YYYY-MM")
    return p


def egd_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    e = p.add_argument_group("EG.D API")
    e.add_argument("--ean", help="metering point EAN (env EGD_EAN)")
    e.add_argument(
        "--profile",
        help="profile code, e.g. ICC1/ICQ2/ISC1 (A, B) or DCQC/DSQC (C1); default: by meter type",
    )
    e.add_argument("--test-env", action="store_true", help="use EG.D's test environment")
    e.add_argument(
        "--api-variant",
        choices=["rest", "c1-legacy"],
        help="endpoint flavour: unified /rest/spotreby (default) or legacy /rest/c/spotreby",
    )
    e.add_argument(
        "--api-timestamps",
        choices=["start", "end"],
        help="whether API timestamps mark interval start or end (default: start)",
    )
    return p


# ------------------------------------------------------------------------------------------
# context
# ------------------------------------------------------------------------------------------
class Context:
    """Configuration, HTTP session and cache for one CLI invocation."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.cfg: Config = load_config(Path(args.config) if args.config else None)
        for w in self.cfg.warnings:
            warn(w)
        self.session = make_session()
        if args.no_cache:
            cache_dir = None
        else:
            cache_dir = Path(args.cache_dir) if args.cache_dir else default_cache_dir()
        self.cache = JsonCache(cache_dir)

    def egd(self) -> EgdClient:
        cid, secret = self.cfg.client_id, self.cfg.client_secret
        if not cid or not secret:
            raise CliError(
                "EG.D API credentials missing. Set EGD_CLIENT_ID and EGD_CLIENT_SECRET (or the "
                "[egd] section of the config file). Generate them at portal.distribuce24.cz → "
                "Správa účtů → Vzdálený přístup – OpenAPI."
            )
        test_env = bool(getattr(self.args, "test_env", False) or self.cfg.egd.get("test_env"))
        return EgdClient(self.session, EgdCredentials(cid, secret), test_env=test_env)

    def egd_readings(self, start: date, end: date, kind: Kind = "consumption") -> LoadResult:
        ean = self.args.ean or self.cfg.ean
        if not ean:
            raise CliError("No EAN given: use --ean, EGD_EAN or [egd] ean in the config file.")
        t0, t1 = local_day_bounds_utc(start, end)
        profile = self.args.profile
        if profile is None and kind == "consumption":
            profile = self.cfg.egd.get("profile")
        return self.egd().readings(
            str(ean),
            t0,
            t1,
            profile=profile,
            variant=self.args.api_variant or self.cfg.egd.get("api_variant", "rest"),
            timestamps=self.args.api_timestamps or self.cfg.egd.get("timestamps"),
            kind=kind,
        )
