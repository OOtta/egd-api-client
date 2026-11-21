from datetime import UTC, date, datetime

from egd_spot.timeutil import (
    HOUR,
    QUARTER,
    days_in_month,
    format_api_utc,
    local_day_bounds_utc,
    parse_iso_utc,
    period_start_utc,
)


def test_period_start_regular_winter_day():
    assert period_start_utc(date(2025, 11, 3), 1, QUARTER) == datetime(
        2025, 11, 2, 23, 0, tzinfo=UTC
    )
    assert period_start_utc(date(2025, 11, 3), 96, QUARTER) == datetime(
        2025, 11, 3, 22, 45, tzinfo=UTC
    )


def test_period_start_dst_end_day_has_100_quarters():
    d = date(2025, 10, 26)
    assert period_start_utc(d, 1, QUARTER) == datetime(2025, 10, 25, 22, 0, tzinfo=UTC)
    # periods 9..12 = 02:00a (CEST), 13..16 = 02:00b (CET)
    assert period_start_utc(d, 9, QUARTER) == datetime(2025, 10, 26, 0, 0, tzinfo=UTC)
    assert period_start_utc(d, 13, QUARTER) == datetime(2025, 10, 26, 1, 0, tzinfo=UTC)
    assert period_start_utc(d, 100, QUARTER) == datetime(2025, 10, 26, 22, 45, tzinfo=UTC)


def test_period_start_dst_start_day_hourly():
    d = date(2024, 3, 31)
    assert period_start_utc(d, 3, HOUR) == datetime(2024, 3, 31, 1, 0, tzinfo=UTC)  # 03:00 CEST
    assert period_start_utc(d, 23, HOUR) == datetime(2024, 3, 31, 21, 0, tzinfo=UTC)


def test_day_bounds_and_formatting():
    a, b = local_day_bounds_utc(date(2025, 7, 1), date(2025, 7, 31))
    assert format_api_utc(a) == "2025-06-30T22:00:00.000Z"
    assert format_api_utc(b) == "2025-07-31T22:00:00.000Z"


def test_parse_iso_utc_variants():
    assert parse_iso_utc("2023-03-01T00:45:00.000Z") == datetime(2023, 3, 1, 0, 45, tzinfo=UTC)
    assert parse_iso_utc("2025-11-22T00:15:00+01:00") == datetime(2025, 11, 21, 23, 15, tzinfo=UTC)
    assert parse_iso_utc("2025-11-22T00:15:00") == datetime(2025, 11, 22, 0, 15, tzinfo=UTC)


def test_days_in_month():
    assert days_in_month(2024, 2) == 29
    assert days_in_month(2025, 12) == 31
