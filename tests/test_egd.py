import json
from datetime import UTC, date, datetime

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
from egd_spot.timeutil import local_day_bounds_utc

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


def test_readings_query_params_chunking_and_status_warning(mocked):
    _token(mocked)
    seen = []

    def cb(request):
        q = dict(p.split("=", 1) for p in request.url.split("?", 1)[1].split("&"))
        seen.append(q)
        start = q["from"].replace("%3A", ":")
        ts = [start]
        return 200, {}, json.dumps(ab_payload(ts, value=1.0, status="IU013"))

    mocked.add_callback(responses.GET, f"{PROD_DATA_URL}/spotreby", callback=cb)
    t0, t1 = local_day_bounds_utc(date(2025, 1, 1), date(2025, 2, 15))  # 46 days → 2 chunks
    res = EgdClient(requests.Session(), CREDS).readings("859182400100366666", t0, t1)
    assert len(seen) == 2
    assert seen[0]["ean"] == "859182400100366666"
    assert seen[0]["profile"] == "ICC1"
    assert seen[0]["from"].replace("%3A", ":") == "2024-12-31T23:00:00.000Z"
    assert seen[0]["to"].replace("%3A", ":") == "2025-01-30T22:45:00.000Z"
    assert seen[0]["pageStart"] == "1" and seen[0]["pageSize"] == "3000"
    assert len(res.readings) == 2
    assert res.readings[0].kwh == pytest.approx(0.25)
    assert "IU013" in res.warnings[0]


def test_readings_pagination(mocked, monkeypatch):
    import egd_spot.egd as egd

    monkeypatch.setattr(egd, "PAGE_SIZE", 2)
    _token(mocked)
    pages = {
        "1": ["2025-01-01T00:00:00.000Z", "2025-01-01T00:15:00.000Z"],
        "3": ["2025-01-01T00:30:00.000Z"],
    }

    def cb(request):
        page = request.url.split("pageStart=")[1].split("&")[0]
        return 200, {}, json.dumps(ab_payload(pages[page], value=4.0))

    mocked.add_callback(responses.GET, f"{PROD_DATA_URL}/spotreby", callback=cb)
    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    t1 = datetime(2025, 1, 1, 1, tzinfo=UTC)
    res = EgdClient(requests.Session(), CREDS).readings("x", t0, t1)
    assert [r.start.minute for r in res.readings] == [0, 15, 30]


def test_legacy_c1_variant_endpoint(mocked):
    _token(mocked)
    mocked.add(responses.GET, f"{PROD_DATA_URL}/c/spotreby", json=[])
    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    EgdClient(requests.Session(), CREDS).readings("x", t0, t0.replace(hour=1), variant="c1-legacy")
    url = mocked.calls[1].request.url
    assert "zdrojDat=ODBERNE_MISTO" in url
    assert "profile=0.0.2.4.1.2.12" in url
