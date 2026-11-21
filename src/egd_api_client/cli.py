"""Command-line interface: ``egd``.

Core commands talk to the EG.D OpenAPI (``meters``, ``profiles``, ``data``); feature
subpackages register their own command groups (``spot`` → :mod:`egd_api_client.spot.cli`).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from egd_api_client import __version__
from egd_api_client.cli_common import (
    CliError,
    Context,
    begin_run,
    clamp_to_yesterday,
    collected_warnings,
    common_parser,
    dump_json,
    egd_parser,
    ensure_unicode_stdio,
    info,
    json_mode,
    period_parser,
    print_json,
    resolve_period,
    warn,
    write_json_file,
)
from egd_api_client.config import ConfigError
from egd_api_client.http import FetchError
from egd_api_client.readings_io import InputFormatError, readings_as_json, write_canonical_csv
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

EG.D credentials: env EGD_CLIENT_ID / EGD_CLIENT_SECRET or the [egd] section of the
config file (default ~/.config/egd-api-client/config.toml).
"""


def cmd_meters(args: argparse.Namespace) -> int:
    points = Context(args).egd().metering_points()
    if args.json:
        print_json({"metering_points": points})
    else:
        for p in points:
            print(f"{p.get('ean', '?')}\tmeasurement type: {p.get('typMereni', '?')}")
    return 0


def cmd_profiles(args: argparse.Namespace) -> int:
    ctx = Context(args)
    profiles = ctx.egd().profiles(args.api_variant or "rest")
    if args.json:
        print_json({"profiles": profiles})
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
    summary = {
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "kind": args.kind,
        "unit": "kWh",
        "values": len(data.readings),
        "total_kwh": round(total, 6),
    }
    out = Path(args.output) if args.output else None
    if out is None:
        if args.json:
            print_json(summary | {"readings": readings_as_json(data.readings)})
        else:
            write_canonical_csv(data.readings, sys.stdout)  # type: ignore[arg-type]
        return 0
    if out.suffix.lower() == ".json":
        write_json_file(out, summary | {"readings": readings_as_json(data.readings)})
    else:
        write_canonical_csv(data.readings, out)
    info(f"Wrote {out}")
    if args.json:
        print_json(summary | {"output": str(out)})
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

    spot_cli.register(sub, common=common, period=period, egd=egd)
    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_unicode_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    begin_run(bool(getattr(args, "json", False)))
    try:
        return int(args.func(args))
    except (CliError, ConfigError, InputFormatError, FetchError) as exc:
        if json_mode():
            print(dump_json({"error": str(exc), "warnings": collected_warnings()}))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
