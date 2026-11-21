"""Command-line interface: ``egd-spot``."""

from __future__ import annotations

import argparse
import calendar
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from egd_spot import __version__
from egd_spot.cache import JsonCache, default_cache_dir
from egd_spot.calculator import compute
from egd_spot.cnb import CnbRates
from egd_spot.config import Config, ConfigError, load_config
from egd_spot.consumption_io import FileOptions, InputFormatError, load_file, write_canonical_csv
from egd_spot.egd import EgdClient, EgdCredentials
from egd_spot.http import FetchError, make_session
from egd_spot.models import LoadResult
from egd_spot.ote import OteClient
from egd_spot.report import (
    export_intervals,
    export_prices,
    export_result,
    fmt,
    render_breakdown,
    render_summary,
)
from egd_spot.tariff import FixedTariff, SpotTariff
from egd_spot.timeutil import local_date, local_day_bounds_utc

EPILOG = """\
examples:
  egd-spot prices --from 2025-11 --export prices.csv
  egd-spot calc --input consumption.csv --multiplier 1.15 --add-czk-mwh 400 --vat
  egd-spot calc --ean 859182400xxxxxxxxx --from 2025-01-01 --to 2025-12-31 \\
                --add-czk-mwh 350 --monthly-fee 99 --vat --breakdown month --fixed-price 3.2
  egd-spot fetch --ean 859182400xxxxxxxxx --from 2025-11 -o nov.csv

EG.D credentials: env EGD_CLIENT_ID / EGD_CLIENT_SECRET or the [egd] section of the
config file (default ~/.config/egd-spot/config.toml).
"""


class CliError(Exception):
    pass


# ------------------------------------------------------------------------------------------
# argument helpers
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


def _first(*values: Any) -> Any:
    return next((v for v in values if v is not None), None)


def build_tariff(args: argparse.Namespace, cfg: Config) -> SpotTariff:
    t = cfg.tariff
    vat = args.vat if args.vat is not None else t.get("vat", 0.0)
    if vat is True:
        vat = 21.0
    return SpotTariff(
        multiplier=float(_first(args.multiplier, t.get("multiplier"), 1.0)),
        add_czk_mwh=float(_first(args.add_czk_mwh, t.get("add_czk_mwh"), 0.0)),
        monthly_fee=float(_first(args.monthly_fee, t.get("monthly_fee"), 0.0)),
        vat_percent=float(vat or 0.0),
        negative=_first(args.negative, t.get("negative"), "as-is"),
    )


def build_fixed(args: argparse.Namespace, cfg: Config) -> FixedTariff | None:
    price = _first(args.fixed_price, cfg.fixed.get("price_czk_kwh"))
    if price is None:
        return None
    fee = _first(args.fixed_monthly_fee, cfg.fixed.get("monthly_fee"), 0.0)
    return FixedTariff(float(price), float(fee))


class Context:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.cfg = load_config(Path(args.config) if args.config else None)
        for w in self.cfg.warnings:
            warn(w)
        self.session = make_session()
        cache_dir = (
            None
            if args.no_cache
            else Path(args.cache_dir)
            if args.cache_dir
            else default_cache_dir()
        )
        self.cache = JsonCache(cache_dir)
        self.cnb = CnbRates(self.session, self.cache)
        self.ote = OteClient(self.session, self.cache, self.cnb)

    def egd(self) -> EgdClient:
        cid, secret = self.cfg.client_id, self.cfg.client_secret
        if not cid or not secret:
            raise CliError(
                "EG.D API credentials missing. Set EGD_CLIENT_ID and EGD_CLIENT_SECRET (or the "
                "[egd] section of the config file). Generate them at portal.distribuce24.cz → "
                "Správa účtů → Vzdálený přístup – OpenAPI. Or use --input with an exported file."
            )
        test_env = bool(self.args.test_env or self.cfg.egd.get("test_env", False))
        return EgdClient(self.session, EgdCredentials(cid, secret), test_env=test_env)

    def egd_readings(self, start: date, end: date) -> LoadResult:
        ean = self.args.ean or self.cfg.ean
        if not ean:
            raise CliError("No EAN given: use --ean, EGD_EAN or [egd] ean in the config file.")
        t0, t1 = local_day_bounds_utc(start, end)
        return self.egd().readings(
            str(ean),
            t0,
            t1,
            profile=self.args.profile or self.cfg.egd.get("profile"),
            variant=self.args.api_variant or self.cfg.egd.get("api_variant", "rest"),
            timestamps=self.args.api_timestamps or self.cfg.egd.get("timestamps"),
        )


def warn_unpriced(ctx: Context) -> None:
    for d, n in sorted(ctx.ote.unpriced.items()):
        warn(
            f"OTE published no market price for {n} period(s) on {d} (emergency state / "
            "stav nouze); consumption in those periods is excluded from the cost."
        )


def warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


def info(msg: str, quiet: bool = False) -> None:
    if not quiet:
        print(msg, file=sys.stderr)


def file_options(args: argparse.Namespace) -> FileOptions:
    return FileOptions(
        time_column=args.time_column,
        value_column=args.value_column,
        unit=args.unit,
        timestamps=args.timestamps,
        interval_minutes=args.interval_minutes,
        sheet=args.sheet,
    )


# ------------------------------------------------------------------------------------------
# commands
# ------------------------------------------------------------------------------------------
def cmd_calc(args: argparse.Namespace) -> int:
    ctx = Context(args)
    tariff = build_tariff(args, ctx.cfg)
    fixed = build_fixed(args, ctx.cfg)

    if args.input:
        data = load_file(Path(args.input), file_options(args))
        start, end = resolve_period(args, data)
        info(f"Loaded {len(data.readings)} readings from {args.input}", args.json)
    else:
        start, end = resolve_period(args)
        yesterday = date.today() - timedelta(days=1)
        if end > yesterday:
            end = yesterday
            warn(f"EG.D provides data only up to yesterday; using --to {end}.")
        if end < start:
            raise CliError("No EG.D data can exist for this period yet.")
        info(f"Downloading EG.D data {start} – {end} ...", args.json)
        data = ctx.egd_readings(start, end)
        if data.available_from is not None:
            start = max(start, local_date(data.available_from))
    for w in data.warnings:
        warn(w)
    if end >= date.today():
        warn("The period includes today or future days; data and prices may be incomplete.")

    resolution = args.price_resolution or ctx.cfg.tariff.get("price_resolution", "auto")
    info(f"Downloading OTE spot prices {start} – {end} ...", args.json)
    prices = ctx.ote.prices(start, end, resolution)
    if not prices:
        raise CliError("No spot prices available for the selected period.")
    warn_unpriced(ctx)

    res = compute(data.readings, prices, tariff, start, end, fixed=fixed, breakdown=args.breakdown)
    for w in res.warnings:
        warn(w)

    if args.json:
        print(json.dumps(res.as_dict(), ensure_ascii=False, indent=2))
    else:
        render_summary(res, sys.stdout)
        render_breakdown(res, sys.stdout)
    if args.export:
        export_result(res, Path(args.export))
        info(f"Wrote {args.export}", args.json)
    if args.intervals:
        export_intervals(res, Path(args.intervals))
        info(f"Wrote {args.intervals}", args.json)
    return 0


def cmd_prices(args: argparse.Namespace) -> int:
    ctx = Context(args)
    start, end = resolve_period(args)
    points = ctx.ote.prices(start, end, args.price_resolution or "auto")
    if not points:
        raise CliError("No spot prices available for the selected period.")
    warn_unpriced(ctx)
    vals = [p.czk_mwh for p in points]
    neg = sum(1 for v in vals if v < 0)
    summary = {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "points": len(points),
        "avg_czk_mwh": round(sum(vals) / len(vals), 2),
        "min_czk_mwh": round(min(vals), 2),
        "max_czk_mwh": round(max(vals), 2),
        "negative_points": neg,
    }
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"Spot prices {start} – {end}: {len(points)} periods")
        print(f"  average {fmt(summary['avg_czk_mwh'])} CZK/MWh")
        print(f"  min     {fmt(summary['min_czk_mwh'])} CZK/MWh")
        print(f"  max     {fmt(summary['max_czk_mwh'])} CZK/MWh")
        print(f"  negative periods: {neg}")
    if args.export:
        export_prices(points, Path(args.export))
        info(f"Wrote {args.export}", args.json)
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    ctx = Context(args)
    start, end = resolve_period(args)
    data = ctx.egd_readings(start, end)
    for w in data.warnings:
        warn(w)
    out = Path(args.output) if args.output else None
    if out:
        write_canonical_csv(data.readings, out)
        info(
            f"Wrote {len(data.readings)} readings "
            f"({sum(r.kwh for r in data.readings):.3f} kWh) to {out}"
        )
    else:
        write_canonical_csv(data.readings, sys.stdout)  # type: ignore[arg-type]
    return 0


def cmd_meters(args: argparse.Namespace) -> int:
    ctx = Context(args)
    points = ctx.egd().metering_points()
    if args.json:
        print(json.dumps(points, ensure_ascii=False, indent=2))
    else:
        for p in points:
            print(f"{p.get('ean', '?')}\tmeasurement type: {p.get('typMereni', '?')}")
    return 0


# ------------------------------------------------------------------------------------------
# parser
# ------------------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    g = common.add_argument_group("general")
    g.add_argument("--config", metavar="FILE", help="config file (TOML); env EGD_SPOT_CONFIG")
    g.add_argument("--cache-dir", metavar="DIR", help="cache directory (default ~/.cache/egd-spot)")
    g.add_argument("--no-cache", action="store_true", help="do not read or write the cache")
    g.add_argument("--json", action="store_true", help="print machine-readable JSON to stdout")

    period = argparse.ArgumentParser(add_help=False)
    p = period.add_argument_group("period (local Czech dates, inclusive)")
    p.add_argument("--from", dest="date_from", metavar="DATE", help="YYYY-MM-DD or YYYY-MM")
    p.add_argument("--to", dest="date_to", metavar="DATE", help="YYYY-MM-DD or YYYY-MM")

    egd = argparse.ArgumentParser(add_help=False)
    e = egd.add_argument_group("EG.D API")
    e.add_argument("--ean", help="metering point EAN (env EGD_EAN)")
    e.add_argument("--profile", help="profile code (default ICC1 = consumption)")
    e.add_argument("--test-env", action="store_true", help="use EG.D's test environment")
    e.add_argument(
        "--api-variant",
        choices=["rest", "c1-legacy"],
        help="endpoint flavour: unified /rest/spotreby (default) or legacy /rest/c/spotreby",
    )
    e.add_argument(
        "--api-timestamps",
        choices=["start", "end"],
        help="whether API timestamps mark interval start or end (default: start; legacy C1: end)",
    )

    parser = argparse.ArgumentParser(
        prog="egd-spot",
        description="What would your electricity have cost on a spot-price tariff? "
        "Uses EG.D smart-meter data and OTE day-ahead prices.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    # calc
    c = sub.add_parser(
        "calc",
        parents=[common, period, egd],
        help="compute the cost of your consumption on a spot tariff",
        description="Compute the cost of your consumption on a spot tariff. Consumption comes "
        "from the EG.D API (--ean) or from a file (--input).",
    )
    src = c.add_argument_group("consumption file (alternative to the API)")
    src.add_argument("-i", "--input", metavar="FILE", help="CSV or XLSX with consumption")
    src.add_argument("--time-column", help="name or 0-based index of the date/time column")
    src.add_argument("--value-column", help="name or 0-based index of the consumption column")
    src.add_argument(
        "--unit",
        choices=["auto", "kwh", "kw"],
        default="auto",
        help="unit of the values (kw = mean power, converted to energy)",
    )
    src.add_argument(
        "--timestamps",
        choices=["auto", "start", "end"],
        default="auto",
        help="timestamps mark the interval start or end (auto: detect)",
    )
    src.add_argument("--interval-minutes", type=int, help="interval length (default: detect)")
    src.add_argument("--sheet", help="XLSX sheet name (default: first)")

    t = c.add_argument_group("spot tariff")
    t.add_argument("-m", "--multiplier", type=float, help="spot price multiplier, e.g. 1.15")
    t.add_argument(
        "-a",
        "--add-czk-mwh",
        type=float,
        metavar="CZK",
        help="fixed surcharge added to the spot price, CZK/MWh, e.g. 400",
    )
    t.add_argument("--monthly-fee", type=float, metavar="CZK", help="supplier fee CZK/month")
    t.add_argument(
        "--vat",
        nargs="?",
        type=float,
        const=21.0,
        metavar="PCT",
        help="add VAT (default 21 %% when given without a value)",
    )
    t.add_argument(
        "--negative",
        choices=["as-is", "clamp-spot", "clamp-total"],
        help="treatment of negative spot prices (default: as-is)",
    )
    t.add_argument(
        "--price-resolution",
        choices=["auto", "hourly"],
        help="auto = 15-min prices where available (from 2025-10-01); hourly = always hourly",
    )

    f = c.add_argument_group("comparison")
    f.add_argument(
        "--fixed-price",
        type=float,
        metavar="CZK_KWH",
        help="compare with a fixed price per kWh (excl. VAT)",
    )
    f.add_argument(
        "--fixed-monthly-fee", type=float, metavar="CZK", help="monthly fee of the fixed tariff"
    )

    o = c.add_argument_group("output")
    o.add_argument("-b", "--breakdown", choices=["none", "day", "month"], default="none")
    o.add_argument("-o", "--export", metavar="FILE", help="write results to .csv or .json")
    o.add_argument("--intervals", metavar="FILE", help="write per-interval details to CSV")
    c.set_defaults(func=cmd_calc)

    # prices
    pr = sub.add_parser("prices", parents=[common, period], help="download/show OTE spot prices")
    pr.add_argument("--price-resolution", choices=["auto", "hourly"])
    pr.add_argument("-o", "--export", metavar="FILE", help="write prices to .csv or .json")
    pr.set_defaults(func=cmd_prices)

    # fetch
    fe = sub.add_parser(
        "fetch", parents=[common, period, egd], help="download EG.D consumption to a CSV file"
    )
    fe.add_argument("-o", "--output", metavar="FILE", help="output CSV (default: stdout)")
    fe.set_defaults(func=cmd_fetch)

    # meters
    me = sub.add_parser(
        "meters",
        parents=[common, egd],
        help="list metering points available to your EG.D credentials",
    )
    me.set_defaults(func=cmd_meters)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (CliError, ConfigError, InputFormatError, FetchError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
