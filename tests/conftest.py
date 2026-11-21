from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import pytest
import requests
import responses

from egd_api_client.cache import JsonCache
from egd_api_client.spot.cnb import CNB_YEAR_URL, CnbRates
from egd_api_client.spot.ote import NS, OTE_SERVICE_URL, QUARTER_HOUR_MARKET_START, OteClient
from egd_api_client.timeutil import local_day_bounds_utc

FIXTURES = Path(__file__).parent / "fixtures"


def periods_in_day(d: date, minutes: int) -> int:
    a, b = local_day_bounds_utc(d, d)
    return int((b - a).total_seconds() // (minutes * 60))


def ote_xml(start: date, end: date, price: Callable[[date, int], float], quarter: bool) -> str:
    items = []
    d = start
    while d <= end:
        if quarter:
            n = periods_in_day(d, 15)
            for i in range(1, n + 1):
                hourly = sum(price(d, (i - 1) // 4 * 4 + k) for k in range(1, 5)) / 4
                items.append(
                    f"<Item><Date>{d}</Date><PeriodResolution>PT15M</PeriodResolution>"
                    f"<PeriodIndex>{i}</PeriodIndex><PeriodInterval>x</PeriodInterval>"
                    f"<Price>{price(d, i):.2f}</Price><HourlyPrice>{hourly:.2f}</HourlyPrice>"
                    f"<VolumeTotal>1.0</VolumeTotal></Item>"
                )
        else:
            for h in range(1, periods_in_day(d, 60) + 1):
                items.append(
                    f"<Item><Date>{d}</Date><Hour>{h}</Hour><Price>{price(d, h):.2f}</Price>"
                    f"<Volume>1.0</Volume></Item>"
                )
        d += timedelta(days=1)
    op = "GetDamPricePeriodEResponse" if quarter else "GetDamPriceEResponse"
    return (
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
        f'<{op} xmlns="{NS}"><Result>{"".join(items)}</Result></{op}></soap:Body></soap:Envelope>'
    )


class FakeOte:
    """responses callback emulating OTE's SOAP service with a price function."""

    def __init__(self, price: Callable[[date, int], float], last_day: date | None = None):
        self.price = price
        self.last_day = last_day
        self.calls: list[tuple[str, date, date]] = []

    def __call__(self, request):  # type: ignore[no-untyped-def]
        body = request.body.decode() if isinstance(request.body, bytes) else request.body
        op = re.search(r"<pub:(GetDam\w+)>", body).group(1)
        start = date.fromisoformat(re.search(r"<pub:StartDate>([^<]+)", body).group(1))
        end = date.fromisoformat(re.search(r"<pub:EndDate>([^<]+)", body).group(1))
        self.calls.append((op, start, end))
        if self.last_day:
            end = min(end, self.last_day)
        if op == "GetDamPricePeriodE":
            start = max(start, QUARTER_HOUR_MARKET_START)
            return 200, {}, ote_xml(start, end, self.price, quarter=True)
        end = min(end, QUARTER_HOUR_MARKET_START - timedelta(days=1))
        price = self.price
        if "<pub:InEur>true</pub:InEur>" in body:  # hourly era in EUR: synthetic rate 25

            def price(d: date, i: int) -> float:
                return self.price(d, i) / 25

        return 200, {}, ote_xml(start, end, price, quarter=False)


def cnb_text() -> str:
    return (FIXTURES / "cnb_2025_sep_oct.txt").read_text(encoding="utf-8")


@pytest.fixture
def mocked() -> responses.RequestsMock:
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        yield rsps


@pytest.fixture
def cache(tmp_path: Path) -> JsonCache:
    return JsonCache(tmp_path / "cache")


@pytest.fixture
def ote_client(cache: JsonCache) -> OteClient:
    s = requests.Session()
    return OteClient(s, cache, CnbRates(s, cache, today=date(2025, 12, 1)))


def add_cnb(rsps: responses.RequestsMock, text: str | None = None) -> None:
    rsps.add(responses.GET, re.compile(re.escape(CNB_YEAR_URL) + r".*"), body=text or cnb_text())


def add_ote(rsps: responses.RequestsMock, fake: FakeOte) -> None:
    rsps.add_callback(responses.POST, OTE_SERVICE_URL, callback=fake)
