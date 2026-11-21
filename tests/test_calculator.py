from datetime import UTC, date, datetime, timedelta

import pytest

from egd_api_client.models import PricePoint, Reading
from egd_api_client.spot.calculator import PriceLookup, compute
from egd_api_client.spot.tariff import FixedTariff, SpotTariff
from egd_api_client.timeutil import HOUR, QUARTER, local_day_bounds_utc

DAY = date(2025, 11, 3)


def quarter_prices(day: date, f):
    t0, t1 = local_day_bounds_utc(day, day)
    out, t, i = [], t0, 0
    while t < t1:
        out.append(PricePoint(t, QUARTER, f(i)))
        t += QUARTER
        i += 1
    return out


def flat_readings(day: date, kwh: float):
    t0, t1 = local_day_bounds_utc(day, day)
    out, t = [], t0
    while t < t1:
        out.append(Reading(t, kwh))
        t += QUARTER
    return out


def test_simple_day_hand_computed():
    prices = quarter_prices(DAY, lambda i: 2000.0)
    readings = flat_readings(DAY, 0.25)  # 24 kWh/day
    res = compute(readings, prices, SpotTariff(multiplier=1.15, add_czk_mwh=400), DAY, DAY)
    assert res.total.kwh == pytest.approx(24)
    # (2000*1.15 + 400) CZK/MWh = 2.7 CZK/kWh
    assert res.total.energy_czk == pytest.approx(24 * 2.7)
    assert res.total.avg_price_czk_kwh == pytest.approx(2.7)
    assert res.covered_intervals == res.expected_intervals == 96
    assert res.warnings == []


def test_consumption_weighted_vs_time_average():
    prices = quarter_prices(DAY, lambda i: 1000.0 if i < 48 else 3000.0)
    readings = [
        Reading(r.start, 0.0 if i < 48 else 1.0) for i, r in enumerate(flat_readings(DAY, 0))
    ]
    res = compute(readings, prices, SpotTariff(), DAY, DAY)
    assert res.avg_spot_time_czk_mwh == pytest.approx(2000)
    assert res.total.avg_spot_czk_mwh == pytest.approx(3000)


def test_hourly_price_applies_to_each_quarter():
    t0, _ = local_day_bounds_utc(DAY, DAY)
    prices = [PricePoint(t0 + h * HOUR, HOUR, 1000.0 * (h + 1)) for h in range(24)]
    lookup = PriceLookup(prices)
    assert lookup.at(t0 + timedelta(minutes=45)).czk_mwh == 1000.0
    assert lookup.at(t0 + timedelta(minutes=60)).czk_mwh == 2000.0


def test_hourly_reading_split_across_15min_prices():
    prices = quarter_prices(DAY, lambda i: [1000.0, 2000.0, 3000.0, 4000.0][i % 4])
    t0, _ = local_day_bounds_utc(DAY, DAY)
    res = compute([Reading(t0, 4.0, HOUR)], prices, SpotTariff(), DAY, DAY)
    assert res.total.energy_czk == pytest.approx(1 + 2 + 3 + 4)


def test_vat_fee_proration_and_fixed_comparison():
    start, end = date(2025, 11, 16), date(2025, 11, 30)  # 15 of 30 days
    prices, readings = [], []
    d = start
    while d <= end:
        prices += quarter_prices(d, lambda i: 2000.0)
        readings += flat_readings(d, 0.1)
        d += timedelta(days=1)
    tariff = SpotTariff(monthly_fee=120, vat_percent=21)
    res = compute(
        readings, prices, tariff, start, end, fixed=FixedTariff(3.0, 60), breakdown="month"
    )
    kwh = 15 * 9.6
    assert res.total.kwh == pytest.approx(kwh)
    assert res.total.fee_czk == pytest.approx(60)
    assert res.total.total_czk == pytest.approx((kwh * 2.0 + 60) * 1.21)
    assert res.total.fixed_total_czk == pytest.approx((kwh * 3.0 + 30) * 1.21)
    assert res.total.savings_czk == pytest.approx(res.total.fixed_total_czk - res.total.total_czk)
    assert [b.label for b in res.buckets] == ["2025-11"]


def test_daily_breakdown_and_negative_prices():
    prices = quarter_prices(DAY, lambda i: -500.0 if i == 50 else 1000.0)
    readings = flat_readings(DAY, 1.0)
    res = compute(readings, prices, SpotTariff(negative="clamp-spot"), DAY, DAY, breakdown="day")
    assert res.total.negative_kwh == pytest.approx(1.0)
    assert res.total.energy_czk == pytest.approx(95 * 1.0)
    assert res.buckets[0].label == "2025-11-03"


def test_missing_prices_and_data_are_reported():
    prices = quarter_prices(DAY, lambda i: 1000.0)[:48]
    readings = flat_readings(DAY, 1.0)[:80]
    res = compute(readings, prices, SpotTariff(), DAY, DAY)
    assert res.total.kwh == pytest.approx(48)
    assert res.missing_price_kwh == pytest.approx(32)
    assert len(res.warnings) == 2


def test_readings_outside_period_ignored():
    prices = quarter_prices(DAY, lambda i: 1000.0)
    outside = Reading(datetime(2025, 11, 5, tzinfo=UTC), 100.0)
    res = compute([*flat_readings(DAY, 0.1), outside], prices, SpotTariff(), DAY, DAY)
    assert res.total.kwh == pytest.approx(9.6)


def test_fixed_vt_nt_split_and_uncovered_days():
    from datetime import UTC, date, datetime, timedelta

    from egd_api_client.lowtariff import LowTariffSchedule, Validity, WeeklySchedule
    from egd_api_client.models import PricePoint, Reading
    from egd_api_client.spot.calculator import compute
    from egd_api_client.spot.tariff import FixedTariff, SpotTariff

    q = timedelta(minutes=15)
    t0 = datetime(2026, 1, 4, 23, 0, tzinfo=UTC)  # Mon 5.1.2026 00:00 local
    readings = [Reading(t0 + i * q, 1.0) for i in range(96)]
    prices = [PricePoint(t0 + i * q, q, 1000.0) for i in range(96)]
    nt = WeeklySchedule.uniform("00:00-06:00")
    sched = LowTariffSchedule([(None, nt)], source="test")
    fixed = FixedTariff(4.0, 0.0, 2.0)
    d = date(2026, 1, 5)
    res = compute(readings, prices, SpotTariff(), d, d, fixed=fixed, low_tariff=sched)
    assert res.total.low_kwh == pytest.approx(24)
    assert res.total.fixed_czk == pytest.approx(24 * 2 + 72 * 4)
    assert res.as_dict()["summary"]["low_tariff_kwh"] == 24

    with pytest.raises(ValueError, match="schedule"):
        compute(readings, prices, SpotTariff(), d, d, fixed=fixed)

    dated = LowTariffSchedule([(Validity((2, 1), (2, 28), (2026, 2026)), nt)])
    with pytest.raises(ValueError, match="no data for 1 day"):
        compute(readings, prices, SpotTariff(), d, d, fixed=fixed, low_tariff=dated)
    # without an NT price it is only a warning
    dated.uncovered.clear()
    res = compute(readings, prices, SpotTariff(), d, d, low_tariff=dated)
    assert any("no data" in w for w in res.warnings)
