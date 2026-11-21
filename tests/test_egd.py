import json
from datetime import UTC, date, datetime, timedelta

import pytest
import requests
import responses

from egd_spot.egd import (
    PROD_DATA_URL,
    PROD_TOKEN_URL,
    TEST_DATA_URL,
    TEST_TOKEN_URL,
    EgdAuthError,
    EgdClient,
    EgdCredentials,
    parse_readings,
)
from egd_spot.timeutil import QUARTER, local_day_bounds_utc, parse_iso_utc

CREDS = EgdCredentials("dummy-id", "dummy-secret")


def ab_payload(timestamps, value=4.0, units="KW", status="IU012"):
    return [
        {
            "ean/eic": "859182400100366666",
            "profile": "ICC1",
            "units": units,
            "total": len(timestamps),
            "data": [{"timestamp": t, "value": value, "status": status} for t in timestamps],
        }
    ]


def test_parse_observed_live_shape_lowercase_units():
    payload = {
        "ean/eic": "859182400000000000",
        "profile": "ICQ2",
        "units": "kWh",
        "total": 2,
        "data": [
            {"timestamp": "2026-10-01T00:00:00Z", "value": 0.03, "status": "W"},
            {"timestamp": "2026-10-01T00:15:00Z", "value": 0.02, "status": "W"},
        ],
    }
    rs = parse_readings(payload)
    assert [r.kwh for r in rs] == [0.03, 0.02]
    kw = dict(payload, profile="ICC1", units="kW")
    assert parse_readings(kw)[0].kwh == pytest.approx(0.0075)


def test_parse_kw_profile_is_converted_to_kwh():
    rs = parse_readings(ab_payload(["2023-03-01T00:45:00.000Z"], value=4.0))
    assert rs[0].kwh == pytest.approx(1.0)
    assert rs[0].start == datetime(2023, 3, 1, 0, 45, tzinfo=UTC)


def test_parse_kwh_profile_and_end_convention():
    rs = parse_readings(ab_payload(["2026-03-25T00:15:00Z"], value=0.3, units="KWH"), "end")
    assert rs[0].kwh == pytest.approx(0.3)
    assert rs[0].start == datetime(2026, 3, 25, 0, 0, tzinfo=UTC)


def test_parse_units_missing_falls_back_to_profile():
    payload = ab_payload(["2023-03-01T00:45:00.000Z"], value=2.0)
    del payload[0]["units"]
    assert parse_readings(payload)[0].kwh == pytest.approx(0.5)


def test_parse_legacy_c1_shape():
    payload = [
        {
            "EAN": "859182400100000000",
            "MeasurementType": "0.0.2.4.1.2.12.0.0.0.0.0.0.0.0.3.72.0",
            "MeasurementsValues": [
                {
                    "SerialNumber": "30000041",
                    "Measurements": [
                        {"datetime": "2025-11-22T00:15:00+01:00", "value": "0", "status": "W"},
                        {"datetime": "2025-11-22T00:30:00+01:00", "value": "0.013", "status": "W"},
                    ],
                }
            ],
        }
    ]
    rs = parse_readings(payload, "end")
    assert [r.kwh for r in rs] == [0.0, 0.013]
    assert rs[0].start == datetime(2025, 11, 21, 23, 0, tzinfo=UTC)


def test_credentials_repr_hides_secret():
    assert "dummy-secret" not in repr(CREDS)


def _token(rsps, url=PROD_TOKEN_URL, token="tok-1"):
    rsps.add(responses.POST, url, json={"access_token": token, "token_type": "bearer"})


def test_token_request_and_bearer_header(mocked):
    _token(mocked)
    mocked.add(responses.GET, f"{PROD_DATA_URL}/om", json=[{"ean": "8591", "typMereni": "C1"}])
    client = EgdClient(requests.Session(), CREDS)
    assert client.metering_points() == [{"ean": "8591", "typMereni": "C1"}]
    body = json.loads(mocked.calls[0].request.body)
    assert body == {
        "grant_type": "client_credentials",
        "client_id": "dummy-id",
        "client_secret": "dummy-secret",
        "scope": "namerena_data_openapi",
    }
    assert mocked.calls[1].request.headers["Authorization"] == "Bearer tok-1"


def test_bad_credentials(mocked):
    mocked.add(responses.POST, PROD_TOKEN_URL, status=401, json={"error": "invalid_client"})
    with pytest.raises(EgdAuthError, match="HTTP 401"):
        EgdClient(requests.Session(), CREDS).token()


def test_expired_token_is_refreshed(mocked):
    mocked.add(responses.POST, PROD_TOKEN_URL, json={"access_token": "old"})
    mocked.add(responses.POST, PROD_TOKEN_URL, json={"access_token": "new"})
    mocked.add(responses.GET, f"{PROD_DATA_URL}/om", status=401)
    mocked.add(responses.GET, f"{PROD_DATA_URL}/om", json=[])
    assert EgdClient(requests.Session(), CREDS).metering_points() == []
    assert mocked.calls[-1].request.headers["Authorization"] == "Bearer new"


def test_test_environment_urls(mocked):
    _token(mocked, TEST_TOKEN_URL)
    mocked.add(responses.GET, f"{TEST_DATA_URL}/om", json=[])
    EgdClient(requests.Session(), CREDS, test_env=True).metering_points()
    assert mocked.calls[0].request.url == TEST_TOKEN_URL


def observed(timestamps, value=0.12, units="kW", status="W", profile="ICC1"):
    """Synthetic response in the shape observed from the live API (single JSON object)."""
    return {
        "ean/eic": "859182400000000000",
        "profile": profile,
        "units": units,
        "total": len(timestamps),
        "data": [{"timestamp": t, "value": value, "status": status} for t in timestamps],
    }


def _qs(request):
    from urllib.parse import parse_qs, urlsplit

    return {k: v[0] for k, v in parse_qs(urlsplit(request.url).query).items()}


def _quarters(t0, t1):
    out, t = [], t0
    while t < t1:
        out.append(t)
        t += QUARTER
    return out


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeSpotreby:
    """Emulates /rest/spotreby as observed live: start-labelled, `to` exclusive, optional
    access window, at most `cap` values per response."""

    def __init__(self, access_from=None, cap=3000):
        self.access_from = access_from
        self.cap = cap
        self.requests = []

    def __call__(self, request):
        q = _qs(request)
        self.requests.append(q)
        t0, t1 = parse_iso_utc(q["from"]), parse_iso_utc(q["to"])
        if self.access_from and t0 < self.access_from:
            msg = "V požadovaném období nemáte oprávnění na data odběrného místa."
            return 400, {}, json.dumps({"error": "validation_error", "message": msg})
        slots = [t for t in _quarters(t0, t1 + timedelta(milliseconds=1)) if t < t1]
        if "pageStart" in q:  # live quirk: pageStart=1 skips the first value
            slots = slots[int(q["pageStart"]) :]
        page = slots[: self.cap]
        return 200, {}, json.dumps(observed([_iso(t) for t in page]))


def test_readings_chunking_exclusive_to_and_start_timestamps(mocked):
    _token(mocked)
    mocked.add(responses.GET, f"{PROD_DATA_URL}/om", json=[{"ean": "8591", "typMereni": "B"}])
    fake = FakeSpotreby()
    mocked.add_callback(responses.GET, f"{PROD_DATA_URL}/spotreby", callback=fake)
    t0, t1 = local_day_bounds_utc(date(2025, 1, 1), date(2025, 2, 15))  # 46 days → 2 chunks
    res = EgdClient(requests.Session(), CREDS).readings("8591", t0, t1, today=date(2026, 1, 1))
    assert len(fake.requests) == 2
    q = fake.requests[0]
    assert q["ean"] == "8591" and q["profile"] == "ICC1"
    assert q["from"] == "2024-12-31T23:00:00.000Z"
    assert q["to"] == "2025-01-30T22:59:59.999Z"  # chunk end minus 1 ms (exclusive)
    assert "pageStart" not in q and "pageSize" not in q  # pageStart=1 drops a value
    assert len(res.readings) == 46 * 96  # nothing lost at chunk boundaries
    assert res.readings[0].start == t0
    assert res.readings[-1].start == t1 - QUARTER
    assert res.readings[0].kwh == pytest.approx(0.03)  # 0.12 kW × 0.25 h
    assert res.warnings == []


def test_non_final_status_warning(mocked):
    _token(mocked)
    mocked.add(
        responses.GET,
        f"{PROD_DATA_URL}/spotreby",
        json=observed(["2025-01-01T00:00:00Z"], status="G"),
    )
    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    res = EgdClient(requests.Session(), CREDS).readings(
        "x", t0, t0 + QUARTER, profile="ICQ2", today=date(2026, 1, 1)
    )
    assert "G×1" in res.warnings[0]


def test_truncated_response_is_continued_from_last_timestamp(mocked, monkeypatch):
    import egd_spot.egd as egd

    monkeypatch.setattr(egd, "TRUNCATION_THRESHOLD", 3)
    _token(mocked)
    fake = FakeSpotreby(cap=3)
    mocked.add_callback(responses.GET, f"{PROD_DATA_URL}/spotreby", callback=fake)
    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    res = EgdClient(requests.Session(), CREDS).readings(
        "x", t0, t0 + 2 * timedelta(hours=1), profile="ICC1", today=date(2026, 1, 1)
    )
    assert [q["from"][11:16] for q in fake.requests] == ["00:00", "00:45", "01:30"]
    assert [r.start for r in res.readings] == _quarters(t0, t0 + 2 * timedelta(hours=1))


def test_period_is_truncated_to_yesterday(mocked):
    _token(mocked)
    fake = FakeSpotreby()
    mocked.add_callback(responses.GET, f"{PROD_DATA_URL}/spotreby", callback=fake)
    t0, t1 = local_day_bounds_utc(date(2026, 11, 19), date(2026, 11, 30))
    res = EgdClient(requests.Session(), CREDS).readings(
        "x", t0, t1, profile="ICC1", today=date(2026, 11, 21)
    )
    assert fake.requests[-1]["to"] == "2026-11-20T22:59:59.999Z"
    assert len(res.readings) == 2 * 96
    assert "up to yesterday" in res.warnings[0]


def test_access_window_is_detected(mocked):
    _token(mocked)
    access = datetime(2026, 6, 10, 22, 0, tzinfo=UTC)  # 2026-06-11 00:00 local
    fake = FakeSpotreby(access_from=access)
    mocked.add_callback(responses.GET, f"{PROD_DATA_URL}/spotreby", callback=fake)
    t0, t1 = local_day_bounds_utc(date(2026, 4, 1), date(2026, 6, 30))
    res = EgdClient(requests.Session(), CREDS).readings(
        "x", t0, t1, profile="ICC1", today=date(2026, 11, 21)
    )
    assert res.readings[0].start == access
    assert res.readings[-1].start == t1 - QUARTER
    assert any("since 2026-06-11" in w for w in res.warnings)


def test_api_error_message_is_surfaced(mocked):
    from egd_spot.egd import EgdRequestError

    _token(mocked)
    msg = 'Datum "from" nesmí být starší než 3 roky.'
    mocked.add(
        responses.GET,
        f"{PROD_DATA_URL}/spotreby",
        status=400,
        json={"error": "validation_error", "message": msg},
    )
    t0 = datetime(2020, 1, 1, tzinfo=UTC)
    with pytest.raises(EgdRequestError, match="3 years"):
        EgdClient(requests.Session(), CREDS).readings(
            "x", t0, t0 + QUARTER, profile="ICC1", today=date(2026, 1, 1)
        )


@pytest.mark.parametrize(("typ", "expected"), [("B", "ICC1"), ("A", "ICC1"), ("C1", "DCQC")])
def test_default_profile_follows_measurement_type(mocked, typ, expected):
    _token(mocked)
    mocked.add(
        responses.GET,
        f"{PROD_DATA_URL}/om",
        json=[{"ean": "1", "typMereni": "B"}, {"ean": "2", "typMereni": typ}],
    )
    assert EgdClient(requests.Session(), CREDS).default_profile("2") == expected


def test_legacy_c1_variant_endpoint(mocked):
    _token(mocked)
    mocked.add(responses.GET, f"{PROD_DATA_URL}/c/spotreby", json=[])
    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    EgdClient(requests.Session(), CREDS).readings(
        "x", t0, t0.replace(hour=1), variant="c1-legacy", today=date(2026, 1, 1)
    )
    url = mocked.calls[1].request.url
    assert "zdrojDat=ODBERNE_MISTO" in url
    assert "profile=0.0.2.4.1.2.12" in url
