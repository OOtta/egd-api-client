"""Human-readable output and file exports."""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from egd_api_client.models import PricePoint
from egd_api_client.spot.calculator import Result
from egd_api_client.timeutil import to_local


def fmt(v: float | None, decimals: int = 2) -> str:
    if v is None:
        return "–"
    return f"{v:,.{decimals}f}".replace(",", " ")


def render_summary(res: Result, out: TextIO) -> None:
    t = res.total
    w = out.write
    w(f"Period:            {res.start.isoformat()} – {res.end.isoformat()} (Europe/Prague)\n")
    w(f"Tariff:            {res.tariff.describe()}\n")
    w(
        f"Data coverage:     {res.covered_intervals}/{res.expected_intervals} quarter-hours"
        f" ({100 * res.covered_intervals / max(res.expected_intervals, 1):.1f} %)\n"
    )
    w("\n")
    w(f"Consumption:       {fmt(t.kwh, 3)} kWh\n")
    w(
        f"Avg spot price:    {fmt(t.avg_spot_czk_mwh)} CZK/MWh "
        "(raw OTE, weighted by your consumption)\n"
    )
    w(
        f"                   {fmt(res.avg_spot_time_czk_mwh)} CZK/MWh "
        "(raw OTE, simple average of the period)\n"
    )

    def kwh(v: float | None) -> str:
        return fmt(None if v is None else v / 1000.0, 4)

    vat = res.tariff.vat_percent

    def vat_line(a: float | None, b: float | None) -> None:
        if vat:
            f = res.tariff.vat_factor
            av = None if a is None else a * f
            bv = None if b is None else b * f
            w(f"                   incl. VAT {vat:g} %: {kwh(av)} / {kwh(bv)} CZK/kWh\n")

    ws, ss = t.avg_spot_tariff_czk_mwh, res.avg_spot_tariff_time_czk_mwh
    w(f"Spot + tariff:     {fmt(ws)} CZK/MWh = {kwh(ws)} CZK/kWh (weighted by your consumption)\n")
    w(f"                   {fmt(ss)} CZK/MWh = {kwh(ss)} CZK/kWh (simple average of the period)\n")
    formula = res.tariff._formula(res.tariff.multiplier, res.tariff.add_czk_mwh)
    if res.tariff.has_negative_coefficients:
        neg = res.tariff._formula(*res.tariff.coefficients(-1.0))
        formula += f", negative spot: {neg}"
    if res.tariff.negative != "as-is":
        formula += f", {res.tariff.negative}"
    w(f"                   ({formula}; excl. VAT and fees)\n")
    vat_line(ws, ss)
    if res.fixed is not None:
        wf, sf = t.avg_fixed_czk_mwh, res.avg_fixed_time_czk_mwh
        w(
            f"Avg fixed price:   {fmt(wf)} CZK/MWh = {kwh(wf)} CZK/kWh "
            "(weighted by your consumption)\n"
        )
        w(
            f"                   {fmt(sf)} CZK/MWh = {kwh(sf)} CZK/kWh "
            "(simple average of the period)\n"
        )
        w(f"                   ({res.fixed.describe()}; excl. VAT and fees)\n")
        vat_line(wf, sf)
    if t.negative_kwh:
        w(f"At negative price: {fmt(t.negative_kwh, 3)} kWh\n")
    if t.low_kwh is not None:
        share = 100 * t.low_kwh / t.kwh if t.kwh else 0.0
        w(f"Low tariff (NT):   {fmt(t.low_kwh, 3)} kWh ({share:.1f} %) – {res.low_tariff}\n")
    w("\n")
    w(f"Energy (spot):     {fmt(t.energy_czk)} CZK\n")
    if t.fee_czk:
        w(f"Monthly fee(s):    {fmt(t.fee_czk)} CZK\n")
    if res.tariff.vat_percent:
        w(f"VAT {res.tariff.vat_percent:g} %:          {fmt(t.vat_czk)} CZK\n")
    w(f"TOTAL:             {fmt(t.total_czk)} CZK\n")
    w(f"Effective price:   {fmt(t.avg_price_czk_kwh, 4)} CZK/kWh")
    w(" (incl. VAT)\n" if res.tariff.vat_percent else " (excl. VAT)\n")
    if res.fixed is not None:
        w("\n")
        w(f"Fixed tariff:      {fmt(t.fixed_total_czk)} CZK ({res.fixed.describe()})\n")
        s = t.savings_czk or 0.0
        verdict = (
            "spot would have been CHEAPER by" if s > 0 else "spot would have been MORE EXPENSIVE by"
        )
        w(f"Difference:        {verdict} {fmt(abs(s))} CZK\n")
    if res.distribution is not None:
        incl = " incl. VAT" if res.tariff.vat_percent else " excl. VAT"
        w("\n")
        w(
            f"Distribution:      {fmt(t.distribution_total_czk)} CZK{incl} "
            f"({res.distribution.describe()}, as entered)\n"
        )
        per_kwh = (t.total_with_distribution_czk or 0.0) / t.kwh if t.kwh else None
        w(
            f"Spot + distrib.:   {fmt(t.total_with_distribution_czk)} CZK "
            f"({fmt(per_kwh, 4)} CZK/kWh)\n"
        )
        if res.fixed is not None:
            fpk = (t.fixed_total_with_distribution_czk or 0.0) / t.kwh if t.kwh else None
            w(
                f"Fixed + distrib.:  {fmt(t.fixed_total_with_distribution_czk)} CZK "
                f"({fmt(fpk, 4)} CZK/kWh)\n"
            )
    else:
        w(
            "\nNote: distribution charges (regulated EG.D fees, POZE, system services, ...) "
            "are\nthe same for any supplier and are NOT included unless you enter them with\n"
            "--distribution-high / --distribution-low / --distribution-monthly-fee.\n"
        )


def render_breakdown(res: Result, out: TextIO) -> None:
    if not res.buckets:
        return
    fixed = res.fixed is not None
    low = res.total.low_kwh is not None
    cols = (
        ["Period", "kWh"]
        + (["NT kWh"] if low else [])
        + ["Spot avg", "Spot+tariff"]
        + (["Fixed avg"] if fixed else [])
        + ["Energy", "Fee", "Total", "CZK/kWh"]
    )
    dist = res.total.distribution_czk is not None
    if dist:
        cols += ["Distrib.", "Total+D"]
    if fixed:
        cols += ["Fixed", "Saved"]
        if dist:
            cols += ["Fixed+D"]
    rows = []
    for b in res.buckets:
        row = (
            [b.label, fmt(b.kwh, 3)]
            + ([fmt(b.low_kwh, 3)] if low else [])
            + [fmt(b.avg_spot_czk_mwh), fmt(b.avg_spot_tariff_czk_mwh)]
            + ([fmt(b.avg_fixed_czk_mwh)] if fixed else [])
            + [
                fmt(b.energy_czk),
                fmt(b.fee_czk),
                fmt(b.total_czk),
                fmt(b.avg_price_czk_kwh, 4),
            ]
        )
        if dist:
            row += [fmt(b.distribution_czk), fmt(b.total_with_distribution_czk)]
        if fixed:
            row += [fmt(b.fixed_total_czk), fmt(b.savings_czk)]
            if dist:
                row += [fmt(b.fixed_total_with_distribution_czk)]
        rows.append(row)
    widths = [max(len(c), *(len(r[i]) for r in rows)) for i, c in enumerate(cols)]
    line = "  ".join(
        c.ljust(widths[0]) if i == 0 else c.rjust(widths[i]) for i, c in enumerate(cols)
    )
    out.write("\n" + line + "\n" + "-" * len(line) + "\n")
    for r in rows:
        out.write(
            "  ".join(v.ljust(widths[0]) if i == 0 else v.rjust(widths[i]) for i, v in enumerate(r))
            + "\n"
        )
    money = ["Total"] + (["Total+D"] if dist else []) + (["Fixed", "Saved"] if fixed else [])
    money += ["Fixed+D"] if fixed and dist else []
    incl = ", ".join(money[:-1]) + " and " + money[-1] if len(money) > 1 else money[0]
    excl = "Energy, Fee and Distrib." if dist else "Energy and Fee"
    vat_note = (
        f"{excl} excl. VAT; {incl} incl. VAT" if res.tariff.vat_percent else "all amounts excl. VAT"
    )
    avg = "Spot avg, Spot+tariff and Fixed avg" if fixed else "Spot avg and Spot+tariff"
    out.write(f"({avg} in CZK/MWh excl. VAT, consumption-weighted; money in CZK; {vat_note})\n")


def export_result(res: Result, path: Path) -> None:
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(res.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return
    rows = [b.as_dict() for b in res.buckets] or [res.total.as_dict()]
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def export_intervals(res: Result, path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["start", "kwh", "spot_czk_mwh", "price_czk_mwh", "cost_czk_excl_vat"])
        for i in res.intervals:
            w.writerow(
                [
                    to_local(i.start).isoformat(),
                    f"{i.kwh:.6g}",
                    f"{i.spot_czk_mwh:.2f}",
                    f"{i.price_czk_mwh:.2f}",
                    f"{i.cost_czk:.6f}",
                ]
            )


def export_prices(points: Sequence[PricePoint], path: Path, currency: str = "czk") -> None:
    """Export prices; the ``price`` column/field is in the chosen currency (per MWh).

    ``eur_czk`` is the ČNB rate used for conversion (15-minute era) or empty when OTE
    published both currencies itself (hourly era).
    """
    eur = currency == "eur"

    def price(p: PricePoint) -> float:
        v = p.eur_mwh if eur else p.czk_mwh
        if v is None:
            raise ValueError(f"no EUR price for {p.start}")
        return v

    unit = "eur_mwh" if eur else "czk_mwh"
    if path.suffix.lower() == ".json":
        data = [
            {
                "start": to_local(p.start).isoformat(),
                "minutes": int(p.length.total_seconds() // 60),
                unit: round(price(p), 4),
                "eur_czk": p.eur_czk,
            }
            for p in points
        ]
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["start", "minutes", unit, "eur_czk"])
        for p in points:
            w.writerow(
                [
                    to_local(p.start).isoformat(),
                    int(p.length.total_seconds() // 60),
                    f"{price(p):.4f}" if not eur else f"{price(p):.2f}",
                    "" if p.eur_czk is None else f"{p.eur_czk:.3f}",
                ]
            )


def export_fx(rows: Sequence[dict], path: Path) -> None:
    """Export per-day EUR/CZK rates (``day, eur_czk, fixing_date, note``)."""
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(list(rows), ensure_ascii=False, indent=1), encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(
            fh, fieldnames=["day", "eur_czk", "fixing_date", "note"], lineterminator="\n"
        )
        w.writeheader()
        for r in rows:
            w.writerow({k: "" if v is None else v for k, v in r.items()})
