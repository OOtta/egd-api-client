import re
from datetime import date

import pytest
import requests
import responses

from conftest import add_cnb, cnb_text
from egd_spot.cnb import CNB_YEAR_URL, CnbRates, parse_year_txt
from egd_spot.http import FetchError


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
