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
    w(f"Avg spot price:    {fmt(t.avg_spot_czk_mwh)} CZK/MWh (weighted by your consumption)\n")
    w(
        f"                   {fmt(res.avg_spot_time_czk_mwh)} CZK/MWh "
        "(simple average of the period)\n"
    )
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
    w(
        "\nNote: distribution charges (regulated EG.D fees, POZE, system services, ...) are\n"
        "the same for any supplier and are NOT included unless you add them via\n"
        "--add-czk-mwh / --monthly-fee.\n"
    )


def render_breakdown(res: Result, out: TextIO) -> None:
    if not res.buckets:
        return
    fixed = res.fixed is not None
    low = res.total.low_kwh is not None
    cols = (
        ["Period", "kWh"]
        + (["NT kWh"] if low else [])
        + ["Spot avg", "Energy", "Fee", "Total", "CZK/kWh"]
    )
    if fixed:
        cols += ["Fixed", "Saved"]
    rows = []
    for b in res.buckets:
        row = (
            [b.label, fmt(b.kwh, 3)]
            + ([fmt(b.low_kwh, 3)] if low else [])
            + [
                fmt(b.avg_spot_czk_mwh),
                fmt(b.energy_czk),
                fmt(b.fee_czk),
                fmt(b.total_czk),
                fmt(b.avg_price_czk_kwh, 4),
            ]
        )
        if fixed:
            row += [fmt(b.fixed_total_czk), fmt(b.savings_czk)]
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
    incl = "Total, Fixed and Saved" if fixed else "Total"
    vat_note = (
        f"Energy and Fee excl. VAT; {incl} incl. VAT"
        if res.tariff.vat_percent
        else "all amounts excl. VAT"
    )
    out.write(f"(Spot avg in CZK/MWh, consumption-weighted; money in CZK; {vat_note})\n")


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
