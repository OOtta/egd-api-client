"""Per-day cache of EG.D metering data (synthetic EAN and values, mocked API)."""

import json
import os
import stat
from datetime import UTC, date, datetime

import pytest
import requests
import responses

from conftest import FakeOte, add_ote
from egd_api_client.cache import JsonCache
from egd_api_client.cli import main
from egd_api_client.client import PROD_DATA_URL, PROD_TOKEN_URL, EgdClient
from egd_api_client.readings_cache import CachedReadings, quarter_hours
from egd_api_client.timeutil import QUARTER
from test_client import CREDS, FakeSpotreby, _iso

EAN = "859000000000009999"  # synthetic
TODAY = date(2026, 11, 21)


@pytest.fixture
def api(mocked):
    mocked.add(responses.POST, PROD_TOKEN_URL, json={"access_token": "t"})
    mocked.add(responses.GET, f"{PROD_DATA_URL}/om", json=[{"ean": EAN, "typMereni": "C1"}])
    fake = FakeSpotreby()
    mocked.add_callback(responses.GET, f"{PROD_DATA_URL}/spotreby", callback=fake)
    return fake


def cached(cache, today=TODAY, final_after=7):
    return CachedReadings(
        cache,
        lambda: EgdClient(requests.Session(), CREDS),
        today=today,
        final_after_days=final_after,
    )


def load(cr, start, end, profile=None):
    return cr.readings(
        EAN,
        start,
        end,
        profile=profile,
        variant="rest",
        timestamps=None,
        kind="consumption",
        test_env=False,
    )


def files(cache):
    return sorted(p.name for p in (cache.directory / "egd").iterdir())


@pytest.mark.parametrize(
    ("day", "n"), [(date(2026, 3, 29), 92), (date(2026, 10, 25), 100), (date(2026, 6, 1), 96)]
)
def test_quarter_hours_dst(day, n):
    assert quarter_hours(day) == n


def test_repeat_run_sends_no_request_at_all(mocked, api, cache):
    first = cached(cache)
    r1 = load(first, date(2026, 3, 28), date(2026, 4, 2))  # incl. 92-quarter DST day
    assert first.stats.downloaded_days == 6 and first.stats.requests >= 2
    n = len(mocked.calls)
    second = cached(cache)
    r2 = load(second, date(2026, 3, 28), date(2026, 4, 2))
    assert len(mocked.calls) == n  # no token, no /om, no /spotreby
    assert second.stats.as_dict() == {
        "cached_days": 6,
        "downloaded_days": 0,
        "requests": 0,
        "not_cached_days": 0,
    }
    assert r1.readings == r2.readings
    assert len(r2.readings) == 5 * 96 + 92


def test_ean_is_not_in_file_names_or_contents_and_permissions(api, cache):
    load(cached(cache), date(2026, 6, 1), date(2026, 6, 2))
    names = files(cache)
    assert ".key" in names
    for name in names:
        assert EAN not in name and EAN[-6:] not in name
        text = (cache.directory / "egd" / name).read_text(encoding="utf-8")
        assert EAN not in text
    meta = next(n for n in names if n.endswith("_meta.json"))
    doc = json.loads((cache.directory / "egd" / meta).read_text(encoding="utf-8"))
    assert doc["ean"] == "…9999" and doc["profiles"] == {"consumption": "DCQC"}
    if os.name == "posix":
        for p in [cache.directory, cache.directory / "egd"]:
            assert stat.S_IMODE(p.stat().st_mode) == 0o700
        for p in (cache.directory / "egd").iterdir():
            assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_recent_days_are_refetched_older_cached(api, cache):
    cr = cached(cache, final_after=7)
    load(cr, date(2026, 11, 10), date(2026, 11, 20))  # 14..20 are ≤ 7 days old
    assert cr.stats.not_cached_days == 7
    api.requests.clear()
    cr = cached(cache, final_after=7)
    load(cr, date(2026, 11, 10), date(2026, 11, 20))
    assert cr.stats.cached_days == 4 and cr.stats.downloaded_days == 7
    assert [q["from"][:13] for q in api.requests] == ["2026-11-13T23"]  # local 14 Nov 00:00


def test_incomplete_or_non_final_days_not_cached(mocked, cache):
    mocked.add(responses.POST, PROD_TOKEN_URL, json={"access_token": "t"})
    payload = {
        "units": "kWh",
        "data": [{"timestamp": "2026-06-01T00:00:00Z", "value": 0.1, "status": "W"}]
        + [  # 2 June local: complete, but status G (not final)
            {
                "timestamp": _iso(datetime(2026, 6, 1, 22, tzinfo=UTC) + i * QUARTER),
                "value": 0.1,
                "status": "G",
            }
            for i in range(96)
        ],
    }
    mocked.add(responses.GET, f"{PROD_DATA_URL}/spotreby", json=payload)
    cr = cached(cache)
    load(cr, date(2026, 6, 1), date(2026, 6, 2), profile="ICQ2")
    assert cr.stats.not_cached_days == 2
    assert not [n for n in files(cache) if n.endswith("-06.json")]


def test_today_is_never_cached(api, cache):
    cr = cached(cache, today=date(2026, 6, 3), final_after=0)
    res = load(cr, date(2026, 6, 1), date(2026, 6, 3))
    assert any("up to yesterday" in w for w in res.warnings)
    doc = next(n for n in files(cache) if n.endswith("2026-06.json"))
    days = json.loads((cache.directory / "egd" / doc).read_text(encoding="utf-8"))["days"]
    assert sorted(days) == ["2026-06-01", "2026-06-02"]


def test_access_start_is_remembered(mocked, cache):
    mocked.add(responses.POST, PROD_TOKEN_URL, json={"access_token": "t"})
    fake = FakeSpotreby(access_from=datetime(2026, 6, 10, 22, 0, tzinfo=UTC))
    mocked.add_callback(responses.GET, f"{PROD_DATA_URL}/spotreby", callback=fake)
    r1 = load(cached(cache), date(2026, 6, 1), date(2026, 6, 30), profile="ICC1")
    n = len(mocked.calls)
    cr = cached(cache)
    r2 = load(cr, date(2026, 6, 1), date(2026, 6, 30), profile="ICC1")
    assert len(mocked.calls) == n and cr.stats.cached_days == 20
    assert r2.available_from == r1.available_from
    assert any("since 2026-06-11" in w for w in r2.warnings)


def test_corrupted_month_warns_and_refetches(api, tmp_path):
    warnings = []
    cache = JsonCache(tmp_path / "c", warn=warnings.append)
    load(cached(cache), date(2026, 6, 1), date(2026, 6, 1))
    month = next(p for p in (cache.directory / "egd").iterdir() if p.name.endswith("06.json"))
    month.write_text("{broken", encoding="utf-8")
    cr = cached(cache)
    load(cr, date(2026, 6, 1), date(2026, 6, 1))
    assert cr.stats.downloaded_days == 1 and len(warnings) == 1


def test_refresh_and_disabled(api, tmp_path):
    load(cached(JsonCache(tmp_path / "c")), date(2026, 6, 1), date(2026, 6, 1))
    cr = cached(JsonCache(tmp_path / "c", refresh=True))
    load(cr, date(2026, 6, 1), date(2026, 6, 1))
    assert cr.stats.cached_days == 0 and cr.stats.downloaded_days == 1
    cr = cached(JsonCache(None))
    res = load(cr, date(2026, 6, 1), date(2026, 6, 1))
    assert len(res.readings) == 96 and cr.stats.downloaded_days == 1


# -- CLI ----------------------------------------------------------------------------------------
@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("EGD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("EGD_CLIENT_ID", "dummy-id")
    monkeypatch.setenv("EGD_CLIENT_SECRET", "dummy-secret")
    monkeypatch.delenv("EGD_EAN", raising=False)
    monkeypatch.delenv("EGD_CONFIG", raising=False)


CALC = ["spot", "calc", "--ean", EAN, "--from", "2025-10-01", "--to", "2025-10-03"]


def test_cli_calc_second_run_zero_egd_and_ote_requests(env, mocked, api, capsys):
    from conftest import add_cnb

    add_cnb(mocked)
    fake_ote = FakeOte(lambda d, i: 40.0)
    add_ote(mocked, fake_ote)
    assert main(CALC) == 0
    err = capsys.readouterr().err
    assert "EG.D data: 0 day(s) from cache, 3 day(s) downloaded" in err
    n = len(mocked.calls)
    assert main([*CALC, "--json"]) == 0
    cap = capsys.readouterr()
    assert cap.err == "" and len(mocked.calls) == n
    data = json.loads(cap.out)
    assert data["cache"]["egd"]["cached_days"] == 3 and data["cache"]["egd"]["requests"] == 0
    assert data["cache"]["ote"]["requests"] == 0
    assert EAN not in json.dumps(data["cache"])
    assert main(["cache", "info", "--json"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["egd_days"] == 3 and info["egd_metering_points"] == 1
    assert main(["cache", "clear", "--egd", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["removed"] == ["egd"]
    assert main([*CALC, "--cache-final-after", "100000"]) == 0  # nothing final → not cached
    assert "3 day(s) downloaded" in capsys.readouterr().err
    assert main(CALC) == 0
    assert "0 day(s) from cache" in capsys.readouterr().err
