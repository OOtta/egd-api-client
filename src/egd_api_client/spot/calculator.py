"""Core cost computation."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Literal

from egd_api_client.models import PricePoint, Reading
from egd_api_client.spot.tariff import FixedTariff, SpotTariff
from egd_api_client.timeutil import (
    QUARTER,
    days_in_month,
    iter_days,
    local_date,
    local_day_bounds_utc,
    month_key,
)

Breakdown = Literal["none", "day", "month"]


@dataclass(slots=True)
class IntervalCost:
    start: datetime
    kwh: float
    spot_czk_mwh: float  # consumption-weighted within the interval
    price_czk_mwh: float  # effective energy price (excl. VAT)
    cost_czk: float  # energy cost (excl. VAT)


@dataclass(slots=True)
class Bucket:
    """Aggregated figures for a period (whole range, one month or one day)."""

    label: str
    kwh: float = 0.0
    energy_czk: float = 0.0  # spot energy cost, excl. VAT
    fee_czk: float = 0.0  # prorated monthly fee, excl. VAT
    spot_weighted_sum: float = 0.0  # Σ kWh × spot CZK/MWh
    negative_kwh: float = 0.0
    fixed_czk: float | None = None  # comparison tariff incl. fee, excl. VAT
    vat_factor: float = 1.0

    @property
    def subtotal_czk(self) -> float:
        return self.energy_czk + self.fee_czk

    @property
    def total_czk(self) -> float:
        return self.subtotal_czk * self.vat_factor

    @property
    def vat_czk(self) -> float:
        return self.total_czk - self.subtotal_czk

    @property
    def avg_price_czk_kwh(self) -> float | None:
        """All-in average (energy + fees, VAT if enabled) per kWh."""
        return self.total_czk / self.kwh if self.kwh else None

    @property
    def avg_energy_price_czk_kwh(self) -> float | None:
        return self.energy_czk * self.vat_factor / self.kwh if self.kwh else None

    @property
    def avg_spot_czk_mwh(self) -> float | None:
        """Consumption-weighted average spot price."""
        return self.spot_weighted_sum / self.kwh if self.kwh else None

    @property
    def fixed_total_czk(self) -> float | None:
        return None if self.fixed_czk is None else self.fixed_czk * self.vat_factor

    @property
    def savings_czk(self) -> float | None:
        """Positive = spot would have been cheaper than the fixed tariff."""
        f = self.fixed_total_czk
        return None if f is None else f - self.total_czk

    def as_dict(self) -> dict[str, float | str | None]:
        r = 4
        out: dict[str, float | str | None] = {
            "period": self.label,
            "kwh": round(self.kwh, 3),
            "avg_spot_czk_mwh": _r(self.avg_spot_czk_mwh, 2),
            "energy_czk": round(self.energy_czk, 2),
            "fee_czk": round(self.fee_czk, 2),
            "vat_czk": round(self.vat_czk, 2),
            "total_czk": round(self.total_czk, 2),
            "avg_price_czk_kwh": _r(self.avg_price_czk_kwh, r),
            "negative_price_kwh": round(self.negative_kwh, 3),
        }
        if self.fixed_czk is not None:
            out["fixed_total_czk"] = _r(self.fixed_total_czk, 2)
            out["savings_czk"] = _r(self.savings_czk, 2)
        return out


def _r(v: float | None, n: int) -> float | None:
    return None if v is None else round(v, n)


@dataclass(slots=True)
class Result:
    start: date
    end: date
    tariff: SpotTariff
    fixed: FixedTariff | None
    total: Bucket
    buckets: list[Bucket]
    intervals: list[IntervalCost]
    avg_spot_time_czk_mwh: float | None  # simple (time-weighted) average over the period
    expected_intervals: int
    covered_intervals: int
    missing_price_kwh: float
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "period": {"from": self.start.isoformat(), "to": self.end.isoformat()},
            "tariff": asdict(self.tariff) | {"description": self.tariff.describe()},
            "fixed_tariff": asdict(self.fixed) if self.fixed else None,
            "summary": self.total.as_dict()
            | {
                "avg_spot_time_weighted_czk_mwh": _r(self.avg_spot_time_czk_mwh, 2),
                "expected_quarter_hours": self.expected_intervals,
                "covered_quarter_hours": self.covered_intervals,
                "missing_price_kwh": round(self.missing_price_kwh, 3),
            },
            "breakdown": [b.as_dict() for b in self.buckets],
            "warnings": self.warnings,
        }


class PriceLookup:
    """Find the price valid for a 15-minute slot (15-minute prices preferred over hourly)."""

    def __init__(self, points: Iterable[PricePoint]) -> None:
        self.quarter: dict[datetime, PricePoint] = {}
        self.hour: dict[datetime, PricePoint] = {}
        for p in points:
            (self.quarter if p.length == QUARTER else self.hour)[p.start] = p

    def at(self, slot_start: datetime) -> PricePoint | None:
        p = self.quarter.get(slot_start)
        if p is not None:
            return p
        hour_start = slot_start.replace(minute=0, second=0, microsecond=0)
        return self.hour.get(hour_start)


def _fee_by_day(start: date, end: date, monthly_fee: float) -> dict[date, float]:
    return {d: monthly_fee / days_in_month(d.year, d.month) for d in iter_days(start, end)}


def compute(
    readings: Sequence[Reading],
    prices: Sequence[PricePoint],
    tariff: SpotTariff,
    start: date,
    end: date,
    *,
    fixed: FixedTariff | None = None,
    breakdown: Breakdown = "none",
) -> Result:
    """Price every reading inside local days ``start``..``end`` (inclusive)."""
    t0, t1 = local_day_bounds_utc(start, end)
    lookup = PriceLookup(prices)
    vat = tariff.vat_factor
    warnings: list[str] = []

    def key(d: date) -> str:
        return month_key(d) if breakdown == "month" else d.isoformat()

    buckets: dict[str, Bucket] = {}
    if breakdown != "none":
        for d in iter_days(start, end):
            buckets.setdefault(key(d), Bucket(key(d), vat_factor=vat))
    total = Bucket(f"{start.isoformat()} – {end.isoformat()}", vat_factor=vat)

    intervals: list[IntervalCost] = []
    missing_kwh = 0.0
    covered: set[datetime] = set()
    for r in readings:
        if not (t0 <= r.start < t1):
            continue
        n = max(1, round(r.length / QUARTER))
        slot_kwh = r.kwh / n
        cost = spot_sum = neg = 0.0
        priced_kwh = 0.0
        for k in range(n):
            slot = r.start + k * QUARTER
            covered.add(slot)
            p = lookup.at(slot)
            if p is None:
                missing_kwh += slot_kwh
                continue
            eff = tariff.energy_price_czk_mwh(p.czk_mwh)
            cost += slot_kwh * eff / 1000.0
            spot_sum += slot_kwh * p.czk_mwh
            priced_kwh += slot_kwh
            if p.czk_mwh < 0:
                neg += slot_kwh
        if priced_kwh == 0 and r.kwh != 0:
            continue
        spot_avg = spot_sum / priced_kwh if priced_kwh else 0.0
        eff_avg = cost * 1000.0 / priced_kwh if priced_kwh else 0.0
        intervals.append(IntervalCost(r.start, priced_kwh, spot_avg, eff_avg, cost))
        targets = [total]
        if breakdown != "none":
            targets.append(buckets[key(local_date(r.start))])
        for b in targets:
            b.kwh += priced_kwh
            b.energy_czk += cost
            b.spot_weighted_sum += spot_sum
            b.negative_kwh += neg
            if fixed is not None:
                b.fixed_czk = (b.fixed_czk or 0.0) + priced_kwh * fixed.price_czk_kwh

    # fixed monthly fees, prorated per day
    for d, fee in _fee_by_day(start, end, tariff.monthly_fee).items():
        total.fee_czk += fee
        if breakdown != "none":
            buckets[key(d)].fee_czk += fee
    if fixed is not None:
        for d, fee in _fee_by_day(start, end, fixed.monthly_fee).items():
            total.fixed_czk = (total.fixed_czk or 0.0) + fee
            if breakdown != "none":
                b = buckets[key(d)]
                b.fixed_czk = (b.fixed_czk or 0.0) + fee

    expected = int((t1 - t0) / QUARTER)
    if missing_kwh > 0:
        warnings.append(
            f"No spot price for {missing_kwh:.3f} kWh of consumption (excluded from cost); "
            "prices for future days are not published yet and emergency periods have none."
        )
    if len(covered) < expected:
        missing_h = (expected - len(covered)) / 4
        warnings.append(
            f"Consumption data covers {len(covered)}/{expected} quarter-hours "
            f"({missing_h:g} h missing) – totals only include the data available."
        )

    # simple time-weighted average spot over the period (all slots that have a price)
    slot = t0
    s = 0.0
    n_slots = 0
    while slot < t1:
        p = lookup.at(slot)
        if p is not None:
            s += p.czk_mwh
            n_slots += 1
        slot += QUARTER
    avg_time = s / n_slots if n_slots else None

    return Result(
        start=start,
        end=end,
        tariff=tariff,
        fixed=fixed,
        total=total,
        buckets=list(buckets.values()),
        intervals=intervals,
        avg_spot_time_czk_mwh=avg_time,
        expected_intervals=expected,
        covered_intervals=len(covered),
        missing_price_kwh=missing_kwh,
        warnings=warnings,
    )
