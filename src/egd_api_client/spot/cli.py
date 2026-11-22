"""``egd spot …`` – spot-price commands (``calc``, ``prices``, ``fx``)."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Any

from egd_api_client.cli_common import (
    CliError,
    Context,
    build_low_tariff,
    clamp_to_yesterday,
    collected_warnings,
    first,
    info,
    low_tariff_parser,
    print_json,
    resolve_period,
    warn,
)
from egd_api_client.config import Config
from egd_api_client.readings_io import FileOptions, load_file
from egd_api_client.spot.calculator import compute
from egd_api_client.spot.cnb import CnbRates
from egd_api_client.spot.ote import QUARTER_HOUR_MARKET_START, OteClient
from egd_api_client.spot.report import (
    export_fx,
    export_intervals,
    export_prices,
    export_result,
    fmt,
    render_breakdown,
    render_summary,
)
from egd_api_client.spot.tariff import DistributionTariff, FixedTariff, SpotTariff
from egd_api_client.timeutil import iter_days, local_date


class SpotContext(Context):
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__(args)
        self.cnb = CnbRates(self.session, self.cache)
        self.ote = OteClient(self.session, self.cache, self.cnb)

    def cache_stats(self) -> dict[str, Any]:
        return {
            "enabled": self.cache.enabled,
            "refresh": self.cache.refresh,
            "ote": self.ote.stats.as_dict(),
            "cnb": self.cnb.stats.as_dict(),
        } | ({"egd": self.readings_cache.stats.as_dict()} if self.readings_cache else {})

    def report_cache(self) -> None:
        """Progress line: delivery days from the cache vs. downloaded (suppressed with --json)."""
        o, c = self.ote.stats, self.cnb.stats
        if not self.cache.enabled:
            how = "cache disabled"
        elif self.cache.refresh:
            how = "cache refreshed"
        else:
            how = f"{o.cached_days} day(s) from cache"
        line = f"OTE prices: {how}, {o.downloaded_days} day(s) downloaded ({o.requests} request(s))"
        if c.cached_years or c.downloaded_years:
            line += (
                f"; ČNB rates: {c.cached_years} year(s) from cache, {c.downloaded_years} downloaded"
            )
        info(line)

    def warn_unpriced(self) -> None:
        for d, n in sorted(self.ote.unpriced.items()):
            warn(
                f"OTE published no market price for {n} period(s) on {d} (emergency state / "
                "stav nouze); consumption in those periods is excluded from the cost."
            )


def build_tariff(args: argparse.Namespace, cfg: Config) -> SpotTariff:
    t = cfg.spot
    vat = args.vat if args.vat is not None else t.get("vat", 0.0)
    if vat is True:
        vat = 21.0
    return SpotTariff(
        multiplier=float(first(args.multiplier, t.get("multiplier"), 1.0)),
        add_czk_mwh=float(first(args.add_czk_mwh, t.get("add_czk_mwh"), 0.0)),
        monthly_fee=float(first(args.monthly_fee, t.get("monthly_fee"), 0.0)),
        vat_percent=float(vat or 0.0),
        negative=first(args.negative, t.get("negative"), "as-is"),
        negative_multiplier=_opt_float(
            first(args.negative_multiplier, t.get("negative_multiplier"))
        ),
        negative_add_czk_mwh=_opt_float(
            first(args.negative_add_czk_mwh, t.get("negative_add_czk_mwh"))
        ),
    )


def _opt_float(v: object) -> float | None:
    return None if v is None else float(v)  # type: ignore[arg-type]


def build_fixed(args: argparse.Namespace, cfg: Config) -> FixedTariff | None:
    price = first(args.fixed_price, cfg.fixed.get("price_czk_kwh"))
    if price is None:
        return None
    fee = first(args.fixed_monthly_fee, cfg.fixed.get("monthly_fee"), 0.0)
    low = _opt_float(first(args.fixed_price_low, cfg.fixed.get("price_low_czk_kwh")))
    return FixedTariff(float(price), float(fee), low)


def build_distribution(args: argparse.Namespace, cfg: Config) -> DistributionTariff | None:
    d = cfg.distribution
    high = _opt_float(first(args.distribution_high, d.get("high_czk_kwh")))
    low = _opt_float(first(args.distribution_low, d.get("low_czk_kwh")))
    fee = _opt_float(first(args.distribution_monthly_fee, d.get("monthly_fee")))
    if high is None and low is None and fee is None:
        return None
    if low is not None and high is None:
        raise CliError("--distribution-low needs --distribution-high (the VT charge) as well.")
    return DistributionTariff(high or 0.0, low, fee or 0.0)


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
    ctx = SpotContext(args)
    tariff = build_tariff(args, ctx.cfg)
    fixed = build_fixed(args, ctx.cfg)
    if fixed is None and args.fixed_price_low is not None:
        raise CliError("--fixed-price-low needs --fixed-price (the high-tariff price) as well.")
    low_tariff = build_low_tariff(args, ctx)
    if fixed is not None and fixed.price_low_czk_kwh is not None and low_tariff is None:
        raise CliError(
            "--fixed-price-low needs the low-tariff hours: give --low-tariff-hours (e.g. "
            '"22:00-06:00") or [low_tariff] hours in the config. EG.D\'s API does not '
            "provide NT times, nothing is assumed."
        )
    distribution = build_distribution(args, ctx.cfg)
    if distribution is not None and distribution.low_czk_kwh is not None and low_tariff is None:
        raise CliError(
            "--distribution-low needs the low-tariff hours: give --low-tariff-hours (e.g. "
            '"22:00-06:00") or [low_tariff] hours in the config. Without NT hours use only '
            "--distribution-high, which then applies to all consumption."
        )
    if low_tariff is not None:
        info(f"Low-tariff periods: {low_tariff.describe()}", args.json)

    source: dict[str, Any]
    if args.input:
        data = load_file(Path(args.input), file_options(args))
        start, end = resolve_period(args, data)
        info(f"Loaded {len(data.readings)} readings from {args.input}", args.json)
        source = {"type": "file", "path": str(args.input), "readings": len(data.readings)}
    else:
        start, end = clamp_to_yesterday(*resolve_period(args))
        info(f"EG.D data {start} – {end} ...", args.json)
        data = ctx.egd_readings(start, end)
        if data.available_from is not None:
            start = max(start, local_date(data.available_from))
        source = {"type": "egd-api", "readings": len(data.readings)}
    for w in data.warnings:
        warn(w)
    if end >= date.today():
        warn("The period includes today or future days; data and prices may be incomplete.")

    resolution = args.price_resolution or ctx.cfg.spot.get("price_resolution", "auto")
    info(f"OTE spot prices {start} – {end} ...", args.json)
    prices = ctx.ote.prices(start, end, resolution)
    ctx.report_cache()
    if not prices:
        raise CliError("No spot prices available for the selected period.")
    ctx.warn_unpriced()

    try:
        res = compute(
            data.readings,
            prices,
            tariff,
            start,
            end,
            fixed=fixed,
            breakdown=args.breakdown,
            low_tariff=low_tariff,
            distribution=distribution,
        )
    except ValueError as exc:
        raise CliError(str(exc)) from exc
    for w in res.warnings:
        warn(w)

    if args.export:
        export_result(res, Path(args.export), warnings=collected_warnings())
        info(f"Wrote {args.export}", args.json)
    if args.json:
        print_json({"source": source} | res.as_dict() | {"cache": ctx.cache_stats()})
    else:
        render_summary(res, sys.stdout)
        render_breakdown(res, sys.stdout)
    if args.intervals:
        export_intervals(res, Path(args.intervals))
        info(f"Wrote {args.intervals}", args.json)
    return 0


def cmd_prices(args: argparse.Namespace) -> int:
    ctx = SpotContext(args)
    start, end = resolve_period(args)
    eur = args.currency == "eur"
    points = ctx.ote.prices(start, end, args.price_resolution or "auto", with_eur=eur)
    ctx.report_cache()
    if not points:
        raise CliError("No spot prices available for the selected period.")
    ctx.warn_unpriced()
    unit = "EUR/MWh" if eur else "CZK/MWh"
    vals = [p.eur_mwh if eur else p.czk_mwh for p in points]
    if any(v is None for v in vals):  # pragma: no cover - OTE publishes EUR for every price
        raise CliError("OTE did not publish EUR prices for part of the period.")
    nums = [float(v) for v in vals if v is not None]
    neg = sum(1 for v in nums if v < 0)
    summary = {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "currency": args.currency.upper(),
        "unit": unit,
        "points": len(points),
        "avg": round(sum(nums) / len(nums), 2),
        "min": round(min(nums), 2),
        "max": round(max(nums), 2),
        "negative_points": neg,
    }
    if args.export:
        export_prices(
            points, Path(args.export), currency=args.currency, warnings=collected_warnings()
        )
        info(f"Wrote {args.export}", args.json)
    if args.json:
        print_json(summary | {"cache": ctx.cache_stats()})
    else:
        print(f"Spot prices {start} – {end}: {len(points)} periods ({unit})")
        print(f"  average {fmt(summary['avg'])} {unit}")
        print(f"  min     {fmt(summary['min'])} {unit}")
        print(f"  max     {fmt(summary['max'])} {unit}")
        print(f"  negative periods: {neg}")
    return 0


def fx_rows(cnb: CnbRates, start: date, end: date) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for d in iter_days(start, end):
        if d < QUARTER_HOUR_MARKET_START:
            rows.append(
                {
                    "day": d.isoformat(),
                    "eur_czk": None,
                    "fixing_date": None,
                    "note": "hourly era: OTE publishes CZK prices directly",
                }
            )
            continue
        fixing_day, rate = cnb.fixing(d)
        rows.append(
            {
                "day": d.isoformat(),
                "eur_czk": rate,
                "fixing_date": fixing_day.isoformat(),
                "note": "" if fixing_day == d else "no fixing that day → last fixing before",
            }
        )
    return rows


def cmd_fx(args: argparse.Namespace) -> int:
    """EUR/CZK rate applied to each delivery day (same output options as ``prices``)."""
    ctx = SpotContext(args)
    start, end = resolve_period(args)
    rows = fx_rows(ctx.cnb, start, end)
    ctx.report_cache()
    rates = [r["eur_czk"] for r in rows if r["eur_czk"] is not None]
    summary: dict[str, Any] = {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "days": len(rows),
        "converted_days": len(rates),
        "days_with_earlier_fixing": sum(1 for r in rows if r["note"].startswith("no fixing")),
        "avg_eur_czk": round(sum(rates) / len(rates), 4) if rates else None,
        "min_eur_czk": min(rates) if rates else None,
        "max_eur_czk": max(rates) if rates else None,
    }
    if args.export:
        export_fx(rows, Path(args.export), warnings=collected_warnings())
        info(f"Wrote {args.export}", args.json)
    if args.json:
        print_json(summary | {"days_detail": rows, "cache": ctx.cache_stats()})
    else:
        print("delivery day     EUR/CZK  ČNB fixing of")
        for r in rows:
            day = date.fromisoformat(r["day"])
            rate = "–" if r["eur_czk"] is None else f"{r['eur_czk']:.3f}"
            print(f"{r['day']} {day:%a}  {rate:>7}  {r['fixing_date'] or '–':<10}  {r['note']}")
        if rates:
            print(
                f"\n{len(rates)} converted day(s): average {summary['avg_eur_czk']:.3f}, "
                f"min {summary['min_eur_czk']:.3f}, max {summary['max_eur_czk']:.3f} CZK/EUR; "
                f"{summary['days_with_earlier_fixing']} day(s) used an earlier fixing"
            )
    return 0


# ------------------------------------------------------------------------------------------
# parser
# ------------------------------------------------------------------------------------------
def register(
    sub: Any,
    *,
    common: argparse.ArgumentParser,
    period: argparse.ArgumentParser,
    egd: argparse.ArgumentParser,
) -> None:
    spot = sub.add_parser(
        "spot",
        help="spot-price tools: cost calculation, OTE prices, EUR/CZK rates",
        description="What would your electricity have cost on a spot-price tariff?",
    )
    ssub = spot.add_subparsers(dest="spot_command", required=True, metavar="COMMAND")

    c = ssub.add_parser(
        "calc",
        parents=[common, period, egd, low_tariff_parser()],
        help="compute the cost of your consumption on a spot tariff",
        description="Compute the cost of your consumption on a spot tariff. Consumption comes "
        "from the EG.D API (--ean / EGD_EAN) or from a file (--input).",
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
        "--negative-multiplier",
        type=float,
        metavar="X",
        help="multiplier used when the spot price is negative (default: same as -m)",
    )
    t.add_argument(
        "--negative-add-czk-mwh",
        type=float,
        metavar="CZK",
        help="surcharge used when the spot price is negative (default: same as -a)",
    )
    t.add_argument(
        "--negative",
        choices=["as-is", "clamp-spot", "clamp-total"],
        help="treatment of negative spot prices (default: as-is); clamp-spot sets the spot to "
        "0 before the coefficients, clamp-total floors the resulting price at 0",
    )
    t.add_argument(
        "--price-resolution",
        choices=["auto", "hourly"],
        help="auto = 15-min prices where available (from 2025-10-01); hourly = always",
    )

    f = c.add_argument_group("comparison")
    f.add_argument(
        "--fixed-price",
        type=float,
        metavar="CZK_KWH",
        help="compare with a fixed price per kWh (excl. VAT); the high-tariff (VT) price "
        "of a two-rate product",
    )
    f.add_argument(
        "--fixed-price-low",
        type=float,
        metavar="CZK_KWH",
        help="low-tariff (NT) price of the fixed product (excl. VAT); needs the NT periods",
    )
    f.add_argument(
        "--fixed-monthly-fee", type=float, metavar="CZK", help="monthly fee of the fixed tariff"
    )

    dg = c.add_argument_group(
        "distribution charges (entered by you, CZK excl. VAT; added to spot and fixed totals)"
    )
    dg.add_argument(
        "--distribution-high",
        type=float,
        metavar="CZK_KWH",
        help="distribution charge per kWh in the high tariff (VT), or for all consumption "
        "when --distribution-low is not given",
    )
    dg.add_argument(
        "--distribution-low",
        type=float,
        metavar="CZK_KWH",
        help="distribution charge per kWh in the low tariff (NT); needs --low-tariff-hours",
    )
    dg.add_argument(
        "--distribution-monthly-fee",
        type=float,
        metavar="CZK",
        help="fixed monthly distribution charges (circuit breaker etc.), prorated by days",
    )

    o = c.add_argument_group("output")
    o.add_argument("-b", "--breakdown", choices=["none", "day", "month"], default="none")
    o.add_argument("-o", "--export", metavar="FILE", help="write results to .csv or .json")
    o.add_argument("--intervals", metavar="FILE", help="write per-interval details to CSV")
    c.set_defaults(func=cmd_calc)

    pr = ssub.add_parser("prices", parents=[common, period], help="download/show OTE spot prices")
    pr.add_argument(
        "--currency",
        choices=["czk", "eur"],
        required=True,
        help="czk: OTE's CZK prices (hourly era) / EUR × ČNB rate of the day (15-min era); "
        "eur: OTE's own EUR prices in both eras",
    )
    pr.add_argument("--price-resolution", choices=["auto", "hourly"])
    pr.add_argument("-o", "--export", metavar="FILE", help="write prices to .csv or .json")
    pr.set_defaults(func=cmd_prices)

    fx = ssub.add_parser(
        "fx",
        parents=[common, period],
        help="show the ČNB EUR/CZK rate applied to each delivery day",
    )
    fx.add_argument("-o", "--export", metavar="FILE", help="write the rates to .csv or .json")
    fx.set_defaults(func=cmd_fx)
