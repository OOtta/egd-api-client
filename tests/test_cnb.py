import re
from datetime import date

import pytest
import requests
import responses

from conftest import add_cnb, cnb_text
from egd_api_client.http import FetchError
from egd_api_client.spot.cnb import CNB_YEAR_URL, CnbRates, parse_year_txt


def test_parse_year_txt():
    rates = parse_year_txt(cnb_text())
    assert rates["2025-09-22"] == pytest.approx(24.25)
    assert rates["2025-09-30"] == pytest.approx(24.34)


def test_parse_handles_repeated_header_and_amount():
    text = "Date|1 USD|100 EUR\n01.01.2024|20|2500\nDate|1 EUR\n02.01.2024|24.5\n"
    assert parse_year_txt(text) == {"2024-01-01": 25.0, "2024-01-02": 24.5}


def test_weekend_uses_last_fixing_and_is_cached(mocked, cache):
    add_cnb(mocked)
    rates = CnbRates(requests.Session(), cache, today=date(2026, 1, 15))
    # 2025-09-27 is a Saturday → Friday 26.09 fixing (24.330); matches OTE's own CZK conversion
    assert rates.eur_czk(date(2025, 9, 27)) == pytest.approx(24.33)
    assert len(mocked.calls) == 1
    again = CnbRates(requests.Session(), cache, today=date(2026, 1, 15))
    assert again.eur_czk(date(2025, 9, 22)) == pytest.approx(24.25)
    assert len(mocked.calls) == 1  # served from cache


def test_unavailable_without_cache_raises(mocked, cache):
    mocked.add(responses.GET, re.compile(re.escape(CNB_YEAR_URL) + ".*"), status=503)
    with pytest.raises(FetchError):
        CnbRates(requests.Session(), cache, today=date(2026, 1, 1)).eur_czk(date(2025, 9, 22))


@pytest.mark.parametrize(
    ("day", "fixing_day", "rate"),
    [
        # business day → that day's own 14:30 fixing
        (date(2025, 9, 26), date(2025, 9, 26), 24.330),
        (date(2025, 10, 27), date(2025, 10, 27), 24.325),
        # weekend → Friday's fixing
        (date(2025, 9, 27), date(2025, 9, 26), 24.330),
        (date(2025, 9, 28), date(2025, 9, 26), 24.330),  # Sunday + St. Wenceslas Day
        (date(2025, 9, 29), date(2025, 9, 29), 24.300),  # Monday → its own fixing, not Friday's
        # public holiday on a weekday (28 Oct, Tuesday) → last fixing before (Monday 27 Oct)
        (date(2025, 10, 28), date(2025, 10, 27), 24.325),
        (date(2025, 10, 29), date(2025, 10, 29), 24.335),
        # month / regime boundary
        (date(2025, 10, 1), date(2025, 10, 1), 24.295),
    ],
)
def test_rate_is_selected_per_delivery_day(mocked, cache, day, fixing_day, rate):
    """OTE's EurRate (GetDamIndexE, "Kurz ČNB pro daný den dodávky") equals the ČNB fixing of
    the delivery day itself, or the last fixing before it on weekends and holidays."""
    add_cnb(mocked)
    rates = CnbRates(requests.Session(), cache, today=date(2026, 1, 15))
    assert rates.fixing(day) == (fixing_day, pytest.approx(rate))
    assert rates.eur_czk(day) == pytest.approx(rate)


def test_every_day_of_a_month_gets_its_own_rate(mocked, cache):
    add_cnb(mocked)
    rates = CnbRates(requests.Session(), cache, today=date(2026, 1, 15))
    published = parse_year_txt(cnb_text())
    for n in range(1, 32):
        d = date(2025, 10, n)
        fixing_day, rate = rates.fixing(d)
        assert fixing_day <= d and (d - fixing_day).days <= 3
        assert rate == pytest.approx(published[fixing_day.isoformat()])
        if d.isoformat() in published:
            assert fixing_day == d
