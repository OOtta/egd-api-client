"""Command-line interface: ``egd``.

Core commands talk to the EG.D OpenAPI (``meters``, ``profiles``, ``data``); feature
subpackages register their own command groups (``spot`` → :mod:`egd_api_client.spot.cli`).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from egd_api_client import __version__
from egd_api_client.cli_common import (
    CliError,
    Context,
    build_low_tariff,
    clamp_to_yesterday,
    common_parser,
    egd_parser,
    info,
    low_tariff_parser,
    period_parser,
    resolve_period,
    warn,
)
from egd_api_client.config import ConfigError
from egd_api_client.http import FetchError
from egd_api_client.lowtariff import HdoClient, format_windows
from egd_api_client.readings_io import InputFormatError, write_canonical_csv, write_json
from egd_api_client.spot import cli as spot_cli
from egd_api_client.timeutil import local_date

EPILOG = """\
examples:
  egd meters
  egd data --from 2026-10 -o october.csv
  egd data --kind production --from 2026-06 --to 2026-08 -o export.json
  egd spot calc --from 2026-01 --to 2026-12 -m 1.15 -a 400 --vat -b month
  egd spot calc --input october.csv --add-czk-mwh 350 --fixed-price 3.2
  egd spot prices --from 2026-11 -o prices.csv
  egd spot fx --from 2026-10-24 --to 2026-11-02 -o fx.csv
  egd hdo --hdo-code 405 --psc 37001 --from 2026-11-16 --to 2026-11-22

EG.D credentials: env EGD_CLIENT_ID / EGD_CLIENT_SECRET or the [egd] section of the
config file (default ~/.config/egd-api-client/config.toml).
"""


def cmd_meters(args: argparse.Namespace) -> int:
    points = Context(args).egd().metering_points()
    if args.json:
        print(json.dumps(points, ensure_ascii=False, indent=2))
    else:
        for p in points:
            print(f"{p.get('ean', '?')}\tmeasurement type: {p.get('typMereni', '?')}")
    return 0


def cmd_profiles(args: argparse.Namespace) -> int:
    ctx = Context(args)
    profiles = ctx.egd().profiles(args.api_variant or "rest")
    if args.json:
        print(json.dumps(profiles, ensure_ascii=False, indent=2))
    else:
        for p in profiles:
            if args.all or p.get("komodita", "ELEKTRINA") == "ELEKTRINA":
                print(f"{p.get('kod', '?'):<8}{p.get('nazev', '')}")
    return 0


def cmd_data(args: argparse.Namespace) -> int:
    ctx = Context(args)
    start, end = clamp_to_yesterday(*resolve_period(args))
    data = ctx.egd_readings(start, end, kind=args.kind)
    for w in data.warnings:
        warn(w)
    total = sum(r.kwh for r in data.readings)
    if data.readings:
        info(
            f"{len(data.readings)} values, {total:.3f} kWh, "
            f"{local_date(data.readings[0].start)} – {local_date(data.readings[-1].start)}"
        )
    out = Path(args.output) if args.output else None
    if out is None:
        if args.json:
            write_json(data.readings, sys.stdout)  # type: ignore[arg-type]
        else:
            write_canonical_csv(data.readings, sys.stdout)  # type: ignore[arg-type]
        return 0
    if out.suffix.lower() == ".json":
        with out.open("w", encoding="utf-8") as fh:
            write_json(data.readings, fh)  # type: ignore[arg-type]
    else:
        write_canonical_csv(data.readings, out)
    info(f"Wrote {out}")
    return 0


WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def cmd_hdo(args: argparse.Namespace) -> int:
    """Show low-tariff (NT) periods from EG.D's public HDO data or a manual schedule."""
    ctx = Context(args)
    if args.list:
        region = args.hdo_region
        if args.psc and not region:
            region = HdoClient(ctx.session, ctx.cache).region_for_psc(args.psc)
        recs = HdoClient(ctx.session, ctx.cache).records()
        codes = sorted(
            {
                (r["region"], r["kodHdo_A"])
                for r in recs
                if not region or r["region"].casefold() == region.casefold()
            }
        )
        if args.json:
            print(json.dumps([{"region": a, "code": b} for a, b in codes], indent=1))
        else:
            for a, b in codes:
                print(f"{a}\t{b}")
        return 0
    sched = build_low_tariff(args, ctx)
    if sched is None:
        raise CliError("Give --hdo-code (with --psc / --hdo-region) or --low-tariff-hours.")
    if args.date_from:
        start, end = resolve_period(args)
        from egd_api_client.timeutil import iter_days

        rows = []
        for d in iter_days(start, end):
            w = sched.windows_for(d)
            rows.append(
                {
                    "day": d.isoformat(),
                    "windows": None if w is None else format_windows(w),
                    "nt_hours": None if w is None else sum(b - a for a, b in w) / 60,
                }
            )
        if args.json:
            print(
                json.dumps(
                    {"schedule": sched.describe(), "days": rows}, ensure_ascii=False, indent=1
                )
            )
            return 0
        print(sched.describe())
        for r in rows:
            day = date.fromisoformat(r["day"])
            if r["windows"] is None:
                print(f"{r['day']} {day:%a}  (no schedule published for this day)")
            else:
                print(f"{r['day']} {day:%a}  {r['nt_hours']:5.2f} h  {r['windows']}")
        return 0
    periods = []
    for validity, weekly in sched.periods:
        periods.append(
            {
                "valid": "always" if validity is None else validity.describe(),
                "days": {
                    WEEKDAYS[i - 1]: format_windows(weekly.days.get(i, ())) for i in range(1, 8)
                },
            }
        )
    if args.json:
        print(
            json.dumps(
                {"schedule": sched.describe(), "periods": periods}, ensure_ascii=False, indent=1
            )
        )
        return 0
    print(sched.describe())
    for p in periods:
        print(f"\nvalid {p['valid']}:")
        for day, w in p["days"].items():
            print(f"  {day}  {w}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    common, period, egd = common_parser(), period_parser(), egd_parser()
    parser = argparse.ArgumentParser(
        prog="egd",
        description="Client for the EG.D (E.ON Distribuce) Distribuce24 OpenAPI: metering data "
        "export and spot-price cost calculation.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    me = sub.add_parser(
        "meters", parents=[common, egd], help="list metering points available to your credentials"
    )
    me.set_defaults(func=cmd_meters)

    pr = sub.add_parser("profiles", parents=[common, egd], help="list EG.D profile codes")
    pr.add_argument("--all", action="store_true", help="include gas profiles")
    pr.set_defaults(func=cmd_profiles)

    da = sub.add_parser(
        "data",
        parents=[common, period, egd],
        help="download 15-minute consumption or production data",
        description="Download 15-minute metering data to CSV (default) or JSON. Timestamps in "
        "the output are interval starts in local time with UTC offset; values are kWh.",
    )
    da.add_argument(
        "-k",
        "--kind",
        choices=["consumption", "production"],
        default="consumption",
        help="consumption from the grid or production/export to the grid (picks the profile)",
    )
    da.add_argument("-o", "--output", metavar="FILE", help="output .csv or .json (default stdout)")
    da.set_defaults(func=cmd_data)

    hd = sub.add_parser(
        "hdo",
        parents=[common, period, low_tariff_parser()],
        help="show low-tariff (NT) times from EG.D's public HDO data",
        description="Low-tariff (NT) periods for an HDO code/command from EG.D's public HDO "
        "data (hdo.distribuce24.cz), or a manual schedule. With --from/--to the windows of each "
        "day are listed. Note: EG.D publishes current schedules only (yearly patterns and "
        "dated records), not a history.",
    )
    hd.add_argument(
        "--list", action="store_true", help="list HDO codes (optionally of --hdo-region / --psc)"
    )
    hd.set_defaults(func=cmd_hdo)

    spot_cli.register(sub, common=common, period=period, egd=egd)
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
