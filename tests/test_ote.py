from datetime import UTC, date, datetime

import pytest

from conftest import FIXTURES, FakeOte, add_cnb, add_ote
from egd_api_client.spot.ote import _chunks, parse_items
from egd_api_client.timeutil import HOUR, PRAGUE, QUARTER


def test_parse_real_hourly_response_dst_start():
    items = parse_items((FIXTURES / "ote_dam_price_e_2024-03-31.xml").read_text(encoding="utf-8"))
    assert len(items) == 23  # spring DST day has 23 hours
    assert items[0] == {"Date": "2024-03-31", "Hour": "1", "Price": "1660.77", "Volume": "2363.8"}


def test_parse_real_15min_response_dst_end():
    items = parse_items(
        (FIXTURES / "ote_dam_price_period_e_2025-10-26.xml").read_text(encoding="utf-8")
    )
    assert len(items) == 100  # autumn DST day has 100 quarter-hours
    assert items[8]["PeriodInterval"] == "02a:00-02a:15"
    assert items[12]["PeriodInterval"] == "02b:00-02b:15"
    assert items[0]["Price"] == "57.77"
    assert items[0]["HourlyPrice"] == "29.42"


def test_soap_fault_raises():
    from egd_api_client.http import FetchError

    xml = (
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
        "<soap:Fault><faultstring>bad date</faultstring></soap:Fault></soap:Body></soap:Envelope>"
    )
    with pytest.raises(FetchError, match="bad date"):
        parse_items(xml)


def test_chunks_split_at_month_boundaries_and_gaps():
    days = [date(2025, 1, d) for d in range(20, 32)] + [date(2025, 2, d) for d in (1, 2, 5)]
    assert list(_chunks(days)) == [
        (date(2025, 1, 20), date(2025, 1, 31)),
        (date(2025, 2, 1), date(2025, 2, 2)),
        (date(2025, 2, 5), date(2025, 2, 5)),
    ]


def test_http_500_soap_fault_is_reported(mocked, ote_client):
    from egd_api_client.http import FetchError
    from egd_api_client.spot.ote import OTE_SERVICE_URL

    fault = (
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
        "<soap:Fault><faultcode>soap:Server</faultcode><faultstring>bad operation - Maximal "
        "range between start and end date is 1 month(s)</faultstring></soap:Fault>"
        "</soap:Body></soap:Envelope>"
    )
    mocked.add("POST", OTE_SERVICE_URL, body=fault, status=500)
    with pytest.raises(FetchError, match="Maximal range"):
        ote_client.prices(date(2024, 1, 1), date(2024, 1, 2))


def test_prices_hourly_era_uses_czk_directly(mocked, ote_client):
    fake = FakeOte(lambda d, i: 1000 + i)
    add_ote(mocked, fake)
    pts = ote_client.prices(date(2024, 3, 31), date(2024, 3, 31))
    assert len(pts) == 23
    assert all(p.length == HOUR and p.eur_czk is None for p in pts)
    assert pts[0].start == datetime(2024, 3, 30, 23, 0, tzinfo=UTC)
    assert pts[0].czk_mwh == pytest.approx(1001)
    assert fake.calls == [("GetDamPriceE", date(2024, 3, 31), date(2024, 3, 31))]


def test_prices_quarter_era_converted_with_cnb(mocked, ote_client):
    add_cnb(mocked)
    add_ote(mocked, FakeOte(lambda d, i: 100.0))
    pts = ote_client.prices(date(2025, 10, 26), date(2025, 10, 26))
    assert len(pts) == 100
    assert {p.length for p in pts} == {QUARTER}
    # Sunday 26.10.2025 → Friday 24.10.2025 fixing from the fixture file
    rate = pts[0].eur_czk
    assert rate is not None
    assert pts[0].czk_mwh == pytest.approx(100.0 * rate)


def test_prices_hourly_resolution_in_quarter_era(mocked, ote_client):
    add_cnb(mocked)
    add_ote(mocked, FakeOte(lambda d, i: float(i)))
    pts = ote_client.prices(date(2025, 10, 26), date(2025, 10, 26), "hourly")
    assert len(pts) == 25
    assert pts[0].eur_mwh == pytest.approx(2.5)  # mean of periods 1..4


def test_regime_boundary_is_split_and_cached(mocked, ote_client):
    add_cnb(mocked)
    fake = FakeOte(lambda d, i: 50.0)
    add_ote(mocked, fake)
    pts = ote_client.prices(date(2025, 9, 30), date(2025, 10, 1))
    assert len(pts) == 24 + 96
    assert [c[0] for c in fake.calls] == ["GetDamPriceE", "GetDamPricePeriodE"]
    ote_client.prices(date(2025, 9, 30), date(2025, 10, 1))
    assert len(fake.calls) == 2  # second run served from cache


def test_incomplete_days_are_not_cached(mocked, ote_client):
    add_cnb(mocked)
    fake = FakeOte(lambda d, i: 50.0, last_day=date(2025, 10, 2))
    add_ote(mocked, fake)
    pts = ote_client.prices(date(2025, 10, 1), date(2025, 10, 3))
    assert len(pts) == 2 * 96
    ote_client.prices(date(2025, 10, 1), date(2025, 10, 3))
    assert fake.calls[-1][1:] == (date(2025, 10, 3), date(2025, 10, 3))  # only the missing day


def test_emergency_periods_are_skipped_but_day_is_cached(mocked, ote_client):
    from egd_api_client.spot.ote import NS, OTE_SERVICE_URL

    items = "".join(
        f"<Item><Date>2025-07-04</Date><Hour>{h}</Hour><Price>1000.00</Price></Item>"
        if h <= 12
        else f"<Item><Date>2025-07-04</Date><Hour>{h}</Hour><Emerg>1</Emerg></Item>"
        for h in range(1, 25)
    )
    xml = (
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
        f'<GetDamPriceEResponse xmlns="{NS}"><Result>{items}</Result></GetDamPriceEResponse>'
        "</soap:Body></soap:Envelope>"
    )
    mocked.add("POST", OTE_SERVICE_URL, body=xml)
    pts = ote_client.prices(date(2025, 7, 4), date(2025, 7, 4))
    assert len(pts) == 12
    assert ote_client.unpriced == {date(2025, 7, 4): 12}
    ote_client.prices(date(2025, 7, 4), date(2025, 7, 4))
    assert len(mocked.calls) == 1  # complete day (incl. emergency markers) was cached


def test_quarter_era_rate_follows_each_delivery_day(mocked, ote_client):
    """A flat 100 EUR/MWh price must be converted with each day's own ČNB rate:
    Fri 24.10. → 24.340, Sat/Sun → Friday's 24.340, Mon 27.10. → 24.325,
    Tue 28.10. (holiday) → Monday's 24.325, Wed 29.10. → 24.335."""
    add_cnb(mocked)
    add_ote(mocked, FakeOte(lambda d, i: 100.0))
    points = ote_client.prices(date(2025, 10, 24), date(2025, 10, 29))
    by_day: dict[str, set[float]] = {}
    for p in points:
        by_day.setdefault(str(p.start.astimezone(PRAGUE).date()), set()).add(round(p.czk_mwh, 3))
    assert by_day == {
        "2025-10-24": {2434.0},
        "2025-10-25": {2434.0},
        "2025-10-26": {2434.0},
        "2025-10-27": {2432.5},
        "2025-10-28": {2432.5},
        "2025-10-29": {2433.5},
    }
    rates = {p.eur_czk for p in points if p.start.astimezone(PRAGUE).date() == date(2025, 10, 28)}
    assert len(rates) == 1 and rates.pop() == pytest.approx(24.325)
