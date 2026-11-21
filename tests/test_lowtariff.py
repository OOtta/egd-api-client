from datetime import UTC, date, datetime, timedelta

import pytest

from egd_api_client.lowtariff import LowTariffError, LowTariffSchedule, czech_holidays, parse_hours
from egd_api_client.timeutil import PRAGUE, QUARTER


def local(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=PRAGUE).astimezone(UTC)


def test_parse_hours_merges_and_crosses_midnight():
    assert parse_hours("22:00-06:00, 13:00-15:00") == ((0, 360), (780, 900), (1320, 1440))
    assert parse_hours("00:00-23:59") == ((0, 1440),)
    assert parse_hours("00:00-24:00") == ((0, 1440),)
    assert parse_hours("01:00-03:00,02:00-04:00") == ((60, 240),)
    for bad in ("25:00-01:00", "10-12", "10:00", "10:00-10:00", "", "1:00:00-2:00:00"):
        with pytest.raises(LowTariffError):
            parse_hours(bad)


def test_czech_holidays_include_easter():
    h = czech_holidays(2025)
    assert {date(2025, 4, 18), date(2025, 4, 21), date(2025, 9, 28), date(2025, 11, 17)} <= h
    assert date(2026, 4, 3) in czech_holidays(2026)  # Good Friday 2026
    assert len(h) == 13


def test_low_fraction_partial_and_weekend():
    s = LowTariffSchedule.parse("22:00-06:20", weekend="00:00-24:00")
    assert s.low_fraction(local(2025, 10, 1, 5, 45), QUARTER) == 1.0
    assert s.low_fraction(local(2025, 10, 1, 6, 15), QUARTER) == pytest.approx(1 / 3)
    assert s.low_fraction(local(2025, 10, 1, 12, 0), QUARTER) == 0.0
    assert s.low_fraction(local(2025, 10, 4, 12, 0), QUARTER) == 1.0  # Saturday
    # an hour-long interval crossing midnight into Saturday
    assert s.low_fraction(local(2025, 10, 3, 23, 30), timedelta(hours=1)) == 1.0


def test_dst_day_uses_local_wall_clock():
    s = LowTariffSchedule.parse("02:00-03:00")
    # 26.10.2025: 02:00–03:00 local happens twice (CEST then CET) – both are NT
    first = datetime(2025, 10, 26, 0, 0, tzinfo=UTC)  # 02:00 CEST
    second = datetime(2025, 10, 26, 1, 0, tzinfo=UTC)  # 02:00 CET
    assert s.low_fraction(first, QUARTER) == 1.0
    assert s.low_fraction(second, QUARTER) == 1.0
    assert s.low_fraction(datetime(2025, 10, 26, 2, 0, tzinfo=UTC), QUARTER) == 0.0


def test_holiday_rule():
    weekday = LowTariffSchedule.parse("00:00-01:00", weekend="00:00-24:00")
    weekend = LowTariffSchedule.parse("00:00-01:00", weekend="00:00-24:00", holidays="weekend")
    t = local(2025, 10, 28, 12, 0)  # Tuesday, public holiday
    assert weekday.low_fraction(t, QUARTER) == 0.0
    assert weekend.low_fraction(t, QUARTER) == 1.0


def test_describe():
    s = LowTariffSchedule.parse("22:00-06:00", weekend="00:00-24:00", holidays="weekend")
    assert s.describe() == "NT 00:00–06:00, 22:00–24:00; Sat/Sun 00:00–24:00; holidays as weekend"
    assert LowTariffSchedule.parse("13:00-15:00").describe() == "NT 13:00–15:00"
