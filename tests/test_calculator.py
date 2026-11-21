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


def test_fixed_vt_nt_split():
    from datetime import UTC, date, datetime, timedelta

    from egd_api_client.lowtariff import LowTariffSchedule
    from egd_api_client.models import PricePoint, Reading
    from egd_api_client.spot.calculator import compute
    from egd_api_client.spot.tariff import FixedTariff, SpotTariff

    q = timedelta(minutes=15)
    t0 = datetime(2026, 1, 4, 23, 0, tzinfo=UTC)  # Mon 5.1.2026 00:00 local
    readings = [Reading(t0 + i * q, 1.0) for i in range(96)]
    prices = [PricePoint(t0 + i * q, q, 1000.0) for i in range(96)]
    sched = LowTariffSchedule.parse("00:00-06:00")
    fixed = FixedTariff(4.0, 0.0, 2.0)
    d = date(2026, 1, 5)
    res = compute(readings, prices, SpotTariff(), d, d, fixed=fixed, low_tariff=sched)
    assert res.total.low_kwh == pytest.approx(24)
    assert res.total.fixed_czk == pytest.approx(24 * 2 + 72 * 4)
    assert res.as_dict()["summary"]["low_tariff_kwh"] == 24
    assert res.as_dict()["low_tariff_schedule"] == "NT 00:00–06:00"

    with pytest.raises(ValueError, match="schedule"):
        compute(readings, prices, SpotTariff(), d, d, fixed=fixed)


def _flat_day():
    from datetime import UTC, date, datetime, timedelta

    from egd_api_client.models import PricePoint, Reading

    q = timedelta(minutes=15)
    t0 = datetime(2026, 1, 4, 23, 0, tzinfo=UTC)  # Mon 5.1.2026 00:00 local
    readings = [Reading(t0 + i * q, 1.0) for i in range(96)]
    prices = [PricePoint(t0 + i * q, q, 1000.0) for i in range(96)]
    return readings, prices, date(2026, 1, 5)


def test_distribution_high_only_applies_to_all_consumption():
    from egd_api_client.spot.calculator import compute
    from egd_api_client.spot.tariff import DistributionTariff, FixedTariff, SpotTariff

    readings, prices, d = _flat_day()
    tariff = SpotTariff(vat_percent=21)
    dist = DistributionTariff(1.5, None, 31.0)  # January: 31 days → 1 CZK for the day
    res = compute(readings, prices, tariff, d, d, fixed=FixedTariff(3.0), distribution=dist)
    t = res.total
    assert t.distribution_czk == pytest.approx(96 * 1.5 + 1.0)
    assert t.total_czk == pytest.approx(96 * 1.0 * 1.21)  # supplier part unchanged
    assert t.total_with_distribution_czk == pytest.approx((96 * 1.0 + 145) * 1.21)
    assert t.fixed_total_with_distribution_czk == pytest.approx((96 * 3.0 + 145) * 1.21)
    assert t.savings_czk == pytest.approx((96 * 3.0 - 96 * 1.0) * 1.21)  # unaffected
    out = res.as_dict()
    assert out["distribution"] == {"high_czk_kwh": 1.5, "low_czk_kwh": None, "monthly_fee": 31.0}
    assert out["summary"]["distribution_czk"] == pytest.approx(145)
    assert out["summary"]["fixed_total_with_distribution_czk"] == pytest.approx(523.93)


def test_distribution_vt_nt_split_needs_schedule():
    from egd_api_client.lowtariff import LowTariffSchedule
    from egd_api_client.spot.calculator import compute
    from egd_api_client.spot.tariff import DistributionTariff, SpotTariff

    readings, prices, d = _flat_day()
    dist = DistributionTariff(2.0, 0.5)
    sched = LowTariffSchedule.parse("22:00-06:00")
    res = compute(readings, prices, SpotTariff(), d, d, low_tariff=sched, distribution=dist,
                  breakdown="day")  # fmt: skip
    # 8 NT hours = 32 kWh at 0.5, 64 kWh at 2.0
    assert res.total.distribution_czk == pytest.approx(32 * 0.5 + 64 * 2.0)
    assert res.buckets[0].distribution_czk == pytest.approx(144)
    assert res.total.total_with_distribution_czk == pytest.approx(96 + 144)
    with pytest.raises(ValueError, match="distribution"):
        compute(readings, prices, SpotTariff(), d, d, distribution=dist)


def test_fixed_weighted_and_simple_average():
    from datetime import UTC, date, datetime, timedelta

    from egd_api_client.lowtariff import LowTariffSchedule
    from egd_api_client.models import PricePoint, Reading
    from egd_api_client.spot.calculator import compute
    from egd_api_client.spot.tariff import FixedTariff, SpotTariff

    q = timedelta(minutes=15)
    t0 = datetime(2026, 1, 4, 23, 0, tzinfo=UTC)  # Mon 5.1.2026 00:00 local
    # 3 kWh per quarter in NT (00:00-06:00), 1 kWh otherwise
    readings = [Reading(t0 + i * q, 3.0 if i < 24 else 1.0) for i in range(96)]
    prices = [PricePoint(t0 + i * q, q, 1000.0) for i in range(96)]
    sched = LowTariffSchedule.parse("00:00-06:00")
    d = date(2026, 1, 5)
    fixed = FixedTariff(4.0, 50.0, 2.0)  # monthly fee must not affect the averages
    res = compute(
        readings, prices, SpotTariff(), d, d, fixed=fixed, low_tariff=sched, breakdown="day"
    )
    # weighted: (72 kWh × 2 + 72 kWh × 4) / 144 kWh = 3 CZK/kWh
    assert res.total.avg_fixed_czk_mwh == pytest.approx(3000)
    assert res.buckets[0].avg_fixed_czk_mwh == pytest.approx(3000)
    # simple: 6 h × 2 + 18 h × 4 over 24 h = 3.5 CZK/kWh
    assert res.avg_fixed_time_czk_mwh == pytest.approx(3500)
    s = res.as_dict()["summary"]
    assert s["avg_fixed_czk_mwh"] == pytest.approx(3000)
    assert s["avg_fixed_time_weighted_czk_mwh"] == pytest.approx(3500)
    assert res.as_dict()["breakdown"][0]["avg_fixed_czk_mwh"] == pytest.approx(3000)

    single = compute(readings, prices, SpotTariff(), d, d, fixed=FixedTariff(4.0))
    assert single.total.avg_fixed_czk_mwh == pytest.approx(4000)
    assert single.avg_fixed_time_czk_mwh == pytest.approx(4000)

    none = compute(readings, prices, SpotTariff(), d, d)
    assert none.total.avg_fixed_czk_mwh is None
    assert "avg_fixed_czk_mwh" not in none.as_dict()["summary"]
    assert "avg_fixed_time_weighted_czk_mwh" not in none.as_dict()["summary"]


def test_spot_tariff_weighted_and_simple_average():
    from datetime import UTC, date, datetime, timedelta

    from egd_api_client.models import PricePoint, Reading
    from egd_api_client.spot.calculator import compute
    from egd_api_client.spot.tariff import SpotTariff

    q = timedelta(minutes=15)
    t0 = datetime(2026, 1, 4, 23, 0, tzinfo=UTC)  # Mon 5.1.2026 00:00 local
    # first half of the day at -100 CZK/MWh (3 kWh/quarter), second half 1000 (1 kWh/quarter)
    readings = [Reading(t0 + i * q, 3.0 if i < 48 else 1.0) for i in range(96)]
    prices = [PricePoint(t0 + i * q, q, -100.0 if i < 48 else 1000.0) for i in range(96)]
    d = date(2026, 1, 5)

    plain = compute(readings, prices, SpotTariff(add_czk_mwh=300), d, d, breakdown="day")
    # linear tariff: weighted spot + 300
    assert plain.total.avg_spot_tariff_czk_mwh == pytest.approx(plain.total.avg_spot_czk_mwh + 300)
    assert plain.avg_spot_tariff_time_czk_mwh == pytest.approx(450 + 300)
    assert plain.buckets[0].avg_spot_tariff_czk_mwh == pytest.approx(
        plain.total.avg_spot_tariff_czk_mwh
    )

    # negative coefficients + clamp-total: -100 × 1.2 + 0 → -120 → floored at 0
    t = SpotTariff(
        multiplier=1.2,
        add_czk_mwh=300,
        negative_add_czk_mwh=0,
        negative="clamp-total",
        monthly_fee=100,
        vat_percent=21,
    )
    res = compute(readings, prices, t, d, d)
    # weighted: (144 kWh × 0 + 48 kWh × 1500) / 192 kWh; fees and VAT not included
    assert res.total.avg_spot_tariff_czk_mwh == pytest.approx(48 * 1500 / 192)
    assert res.total.avg_spot_tariff_czk_mwh == pytest.approx(
        res.total.energy_czk * 1000 / res.total.kwh
    )
    assert res.avg_spot_tariff_time_czk_mwh == pytest.approx((0 + 1500) / 2)
    s = res.as_dict()["summary"]
    assert s["avg_spot_tariff_czk_mwh"] == pytest.approx(375)
    assert s["avg_spot_tariff_time_weighted_czk_mwh"] == pytest.approx(750)
