import re
from datetime import UTC, date, datetime, timedelta

import pytest
import requests
import responses

from egd_api_client.lowtariff import (
    HDO_REGION_URL,
    HDO_TIMES_URL,
    HdoClient,
    LowTariffError,
    czech_holidays,
    manual_schedule,
    parse_hours,
)
from egd_api_client.timeutil import PRAGUE, QUARTER


def local(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=PRAGUE).astimezone(UTC)


def test_parse_hours_merges_and_crosses_midnight():
    assert parse_hours("22:00-06:00, 13:00-15:00") == ((0, 360), (780, 900), (1320, 1440))
    assert parse_hours("00:00-23:59") == ((0, 1440),)
    assert parse_hours("00:00-24:00") == ((0, 1440),)
    assert parse_hours("01:00-03:00,02:00-04:00") == ((60, 240),)
    for bad in ("25:00-01:00", "10-12", "10:00", "10:00-10:00"):
        with pytest.raises(LowTariffError):
            parse_hours(bad)


def test_czech_holidays_include_easter():
    h = czech_holidays(2025)
    assert {date(2025, 4, 18), date(2025, 4, 21), date(2025, 9, 28), date(2025, 11, 17)} <= h
    assert date(2026, 4, 3) in czech_holidays(2026)  # Good Friday 2026
    assert len(h) == 13


def test_low_fraction_partial_and_weekend():
    s = manual_schedule("22:00-06:20", weekend="00:00-24:00")
    assert s.low_fraction(local(2025, 10, 1, 5, 45), QUARTER) == 1.0
    assert s.low_fraction(local(2025, 10, 1, 6, 15), QUARTER) == pytest.approx(1 / 3)
    assert s.low_fraction(local(2025, 10, 1, 12, 0), QUARTER) == 0.0
    assert s.low_fraction(local(2025, 10, 4, 12, 0), QUARTER) == 1.0  # Saturday
    # an hour-long interval crossing midnight into Saturday
    assert s.low_fraction(local(2025, 10, 3, 23, 30), timedelta(hours=1)) == 1.0


def test_dst_day_uses_local_wall_clock():
    s = manual_schedule("02:00-03:00")
    # 26.10.2025: 02:00–03:00 local happens twice (CEST then CET) – both are NT
    first = datetime(2025, 10, 26, 0, 0, tzinfo=UTC)  # 02:00 CEST
    second = datetime(2025, 10, 26, 1, 0, tzinfo=UTC)  # 02:00 CET
    assert s.low_fraction(first, QUARTER) == 1.0
    assert s.low_fraction(second, QUARTER) == 1.0
    assert s.low_fraction(datetime(2025, 10, 26, 2, 0, tzinfo=UTC), QUARTER) == 0.0


def test_holiday_rule():
    weekday = manual_schedule("00:00-01:00", weekend="00:00-24:00")
    sunday = manual_schedule("00:00-01:00", weekend="00:00-24:00", holidays="sunday")
    t = local(2025, 10, 28, 12, 0)  # Tuesday, public holiday
    assert weekday.low_fraction(t, QUARTER) == 0.0
    assert sunday.low_fraction(t, QUARTER) == 1.0


def _rec(region, code, sazba, od, do, times, *, b="A0B0DP0"):
    return {
        "region": region, "kodHdo_A": code, "kodHdo_B": b, "skupinaPovelu": code,
        "od": dict(zip(("den", "mesic", "rok"), od, strict=True)),
        "do": dict(zip(("den", "mesic", "rok"), do, strict=True)),
        "sazby": [{"sazba": sazba, "dny": [
            {"denVTydnu": d, "casy": [{"od": a, "do": z} for a, z in times.get(d, times["*"])]}
            for d in range(1, 8)]}],
    }  # fmt: skip


HDO = [
    # classic code 405: winter / summer patterns repeating every year
    _rec("ZAPAD", "405", "D25d", ("01", "10", "9999"), ("31", "03", "9999"),
         {"*": [("00:00:00", "04:00:00"), ("20:00:00", "23:59:00")]}),
    _rec("ZAPAD", "405", "D25d", ("01", "04", "9999"), ("30", "09", "9999"),
         {"*": [("01:00:00", "05:00:00"), ("21:00:00", "23:59:00")],
          7: [("00:00:00", "23:59:00")]}),
    # same code number in another region
    _rec("VYCHOD", "405", "D25d", ("01", "01", "9999"), ("31", "12", "9999"),
         {"*": [("10:00:00", "12:00:00")]}),
    # smart command with a dated record and two relays
    _rec("SM", "Cd56", "D56d-relé1-TAR", ("01", "09", "2026"), ("25", "10", "2026"),
         {"*": [("00:00:00", "08:00:00"), ("09:00:00", "23:59:00")]}),
    _rec("SM", "Cd56", "D56d-relé2-TUV", ("01", "09", "2026"), ("25", "10", "2026"),
         {"*": [("02:00:00", "04:00:00")]}),
]  # fmt: skip
REGIONS = [{"PSC": "37001", "Region": "ZAPAD"}, {"PSC": "60200", "Region": "VYCHOD"}]


@pytest.fixture
def hdo(mocked, cache):
    mocked.add(responses.GET, HDO_TIMES_URL, json=HDO)
    mocked.add(responses.GET, HDO_REGION_URL, json=REGIONS)
    return HdoClient(requests.Session(), cache, today=date(2026, 11, 21))


def test_hdo_schedule_by_psc_with_seasons_and_holidays(hdo):
    s = hdo.schedule("405", psc="37001")
    assert "region ZAPAD" in s.describe() and "holidays as sunday" in s.describe()
    assert s.windows_for(date(2026, 11, 18)) == ((0, 240), (1200, 1440))  # winter, Wed
    assert s.windows_for(date(2026, 7, 1)) == ((60, 300), (1260, 1440))  # summer, Wed
    assert s.windows_for(date(2026, 7, 5)) == ((0, 1440),)  # Sunday
    assert s.windows_for(date(2026, 7, 6)) == ((0, 1440),)  # Monday holiday → Sunday
    assert s.low_fraction(local(2026, 11, 18, 3, 45), QUARTER) == 1.0
    assert s.low_fraction(local(2026, 11, 18, 4, 0), QUARTER) == 0.0


def test_hdo_ambiguous_region_and_relay(hdo):
    with pytest.raises(LowTariffError, match="several regions"):
        hdo.schedule("405")
    assert hdo.schedule("405", region="vychod").windows_for(date(2026, 1, 1)) == ((600, 720),)
    with pytest.raises(LowTariffError, match="--hdo-relay"):
        hdo.schedule("cd56")
    s = hdo.schedule("CD56", relay="D56d-relé2-TUV")
    assert "holidays as weekday" in s.describe()
    assert s.windows_for(date(2026, 10, 1)) == ((120, 240),)
    assert s.windows_for(date(2026, 8, 31)) is None  # dated record does not cover it
    s.low_fraction(local(2026, 8, 31, 3, 0), QUARTER)
    assert s.uncovered == {date(2026, 8, 31)}


def test_hdo_errors_and_cache(hdo, mocked, cache):
    with pytest.raises(LowTariffError, match="not found"):
        hdo.schedule("999")
    with pytest.raises(LowTariffError, match="PSČ"):
        hdo.schedule("405", psc="11000")
    calls = len(mocked.calls)
    again = HdoClient(requests.Session(), cache, today=date(2026, 11, 21))
    again.schedule("405", region="ZAPAD")
    assert len(mocked.calls) == calls  # served from today's cache


def test_hdo_unavailable_without_cache(mocked, cache):
    from egd_api_client.http import FetchError

    mocked.add(responses.GET, re.compile(re.escape(HDO_TIMES_URL)), status=503)
    with pytest.raises(FetchError):
        HdoClient(requests.Session(), cache).records()
