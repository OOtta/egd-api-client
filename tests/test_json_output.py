"""--json: stdout is exactly one JSON document, warnings inside, nothing on stderr."""

import json
from datetime import date, timedelta

import responses

from conftest import FakeOte, add_cnb, add_ote
from egd_api_client.cli import main
from egd_api_client.client import PROD_DATA_URL, PROD_TOKEN_URL
from test_cli import sample_csv


def _ote(mocked):
    add_cnb(mocked)
    add_ote(mocked, FakeOte(lambda d, i: 1000.0 if d < date(2025, 10, 1) else 40.0))


def test_calc_json_only_json_with_warnings(mocked, tmp_path, capsys):
    _ote(mocked)
    out = tmp_path / "r.json"
    # one day before the data starts → coverage gap warning
    argv = ["spot", "calc", "-i", str(sample_csv(tmp_path)), "--from", "2025-09-29"]
    argv += ["--to", "2025-10-01", "--low-tariff-hours", "22:00-06:00", "--json"]
    assert main([*argv, "-o", str(out)]) == 0
    cap = capsys.readouterr()
    assert cap.err == ""
    data = json.loads(cap.out)
    assert any("quarter-hours" in w for w in data["warnings"])
    assert data["low_tariff_schedule"] == "NT 00:00–06:00, 22:00–24:00"
    assert data["source"] == {"type": "file", "path": str(tmp_path / "c.csv"), "readings": 192}
    assert list(data)[-1] == "warnings"
    assert json.loads(out.read_text(encoding="utf-8"))["warnings"] == data["warnings"]


def test_calc_text_mode_still_warns_on_stderr(mocked, tmp_path, capsys):
    _ote(mocked)
    argv = ["spot", "calc", "-i", str(sample_csv(tmp_path)), "--from", "2025-09-29"]
    out = tmp_path / "r.json"
    assert main([*argv, "--to", "2025-10-01", "-o", str(out)]) == 0
    err = capsys.readouterr().err
    assert "warning:" in err and "Wrote" in err
    assert any("quarter-hours" in w for w in json.loads(out.read_text("utf-8"))["warnings"])


def test_json_error_goes_to_stdout_as_json(tmp_path, capsys):
    argv = ["spot", "calc", "-i", str(sample_csv(tmp_path)), "--json"]
    assert main([*argv, "--fixed-price", "4", "--fixed-price-low", "2"]) == 2
    cap = capsys.readouterr()
    assert cap.err == ""
    data = json.loads(cap.out)
    assert "--low-tariff-hours" in data["error"] and data["warnings"] == []


def _egd(mocked, monkeypatch):
    monkeypatch.setenv("EGD_CLIENT_ID", "id")
    monkeypatch.setenv("EGD_CLIENT_SECRET", "secret")
    mocked.add(responses.POST, PROD_TOKEN_URL, json={"access_token": "t"})
    mocked.add(responses.GET, f"{PROD_DATA_URL}/om", json=[{"ean": "859", "typMereni": "B"}])


def test_meters_and_profiles_json_objects(mocked, monkeypatch, capsys):
    _egd(mocked, monkeypatch)
    mocked.add(
        responses.GET,
        f"{PROD_DATA_URL}/profily",
        json=[{"kod": "ICC1", "nazev": "Činná spotřeba", "komodita": "ELEKTRINA"}],
    )
    assert main(["meters", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "metering_points": [{"ean": "859", "typMereni": "B"}],
        "warnings": [],
    }
    assert main(["profiles", "--json"]) == 0
    cap = capsys.readouterr()
    assert json.loads(cap.out)["profiles"][0]["kod"] == "ICC1" and cap.err == ""


def test_data_json_with_clamp_warning(mocked, monkeypatch, capsys):
    _egd(mocked, monkeypatch)
    yesterday = date.today() - timedelta(days=1)
    ts = f"{yesterday.isoformat()}T10:00:00Z"
    mocked.add(
        responses.GET,
        f"{PROD_DATA_URL}/spotreby",
        json={"units": "kWh", "data": [{"timestamp": ts, "value": 0.25}]},
    )
    argv = ["data", "--ean", "859", "--from", yesterday.isoformat()]
    assert main([*argv, "--to", date.today().isoformat(), "--json"]) == 0
    cap = capsys.readouterr()
    assert cap.err == ""
    data = json.loads(cap.out)
    assert data["period"] == {"from": yesterday.isoformat(), "to": yesterday.isoformat()}
    assert any("only up to yesterday" in w for w in data["warnings"])
    assert data["readings"][0]["kwh"] == 0.25


def test_prices_and_fx_json_with_export(mocked, tmp_path, capsys):
    _ote(mocked)
    out = tmp_path / "p.json"
    argv = ["spot", "prices", "--currency", "eur", "--from", "2025-10-01", "--to", "2025-10-01"]
    assert main([*argv, "--json", "-o", str(out)]) == 0
    cap = capsys.readouterr()
    assert cap.err == "" and json.loads(cap.out)["warnings"] == []
    assert json.loads(out.read_text("utf-8"))["prices"][0]["eur_mwh"] == 40.0
    assert main(["spot", "fx", "--from", "2025-10-24", "--to", "2025-10-25", "--json"]) == 0
    cap = capsys.readouterr()
    assert cap.err == "" and json.loads(cap.out)["warnings"] == []
