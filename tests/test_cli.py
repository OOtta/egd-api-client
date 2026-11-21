import json
from datetime import date

import pytest
import responses

from conftest import FakeOte, add_cnb, add_ote
from egd_spot.cli import main, parse_date_arg
from egd_spot.egd import PROD_DATA_URL, PROD_TOKEN_URL


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("EGD_SPOT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    for v in ("EGD_CLIENT_ID", "EGD_CLIENT_SECRET", "EGD_EAN", "EGD_SPOT_CONFIG"):
        monkeypatch.delenv(v, raising=False)


def test_parse_date_arg():
    assert parse_date_arg("2024-02") == date(2024, 2, 1)
    assert parse_date_arg("2024-02", end=True) == date(2024, 2, 29)
    assert parse_date_arg("2024-02-10") == date(2024, 2, 10)


def sample_csv(tmp_path):
    lines = ["Datum a čas;Činná spotřeba (kWh)"]
    # 2025-09-30 (hourly prices) 00:15 .. 2025-10-01 24:00, 0.25 kWh each quarter
    for day in ("30.09.2025", "01.10.2025"):
        for q in range(1, 97):
            h, m = divmod(q * 15, 60)
            lines.append(f"{day} {h:02d}:{m:02d};0,25")
    p = tmp_path / "c.csv"
    p.write_text("\n".join(lines))
    return p


def test_calc_from_file_json_and_exports(mocked, tmp_path, capsys):
    add_cnb(mocked)
    add_ote(mocked, FakeOte(lambda d, i: 2000.0 if d < date(2025, 10, 1) else 100.0))
    out_json = tmp_path / "r.json"
    out_csv = tmp_path / "i.csv"
    rc = main(
        [
            "calc",
            "-i",
            str(sample_csv(tmp_path)),
            "-m",
            "1.15",
            "-a",
            "400",
            "--vat",
            "--fixed-price",
            "3",
            "-b",
            "day",
            "--json",
            "-o",
            str(out_json),
            "--intervals",
            str(out_csv),
        ]
    )
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["period"] == {"from": "2025-09-30", "to": "2025-10-01"}
    assert data["summary"]["kwh"] == pytest.approx(48)
    day1 = data["breakdown"][0]
    # 24 kWh × (2000×1.15+400)/1000 × 1.21
    assert day1["total_czk"] == pytest.approx(24 * 2.7 * 1.21, abs=0.01)
    day2 = data["breakdown"][1]
    rate = 24.295  # ČNB fixing 01.10.2025
    assert day2["total_czk"] == pytest.approx(
        24 * (100 * rate * 1.15 + 400) / 1000 * 1.21, abs=0.01
    )
    assert json.loads(out_json.read_text())["summary"]["kwh"] == pytest.approx(48)
    assert len(out_csv.read_text().splitlines()) == 1 + 192


def test_calc_text_output(mocked, tmp_path, capsys):
    add_cnb(mocked)
    add_ote(mocked, FakeOte(lambda d, i: 1000.0 if d < date(2025, 10, 1) else 40.0))
    assert main(["calc", "-i", str(sample_csv(tmp_path)), "-b", "month", "--fixed-price", "9"]) == 0
    out = capsys.readouterr().out
    assert "TOTAL:" in out
    assert "CHEAPER" in out
    assert "2025-09" in out and "2025-10" in out


def test_calc_api_requires_credentials(capsys):
    rc = main(["calc", "--ean", "859", "--from", "2025-01"])
    assert rc == 2
    assert "EGD_CLIENT_ID" in capsys.readouterr().err


def test_calc_via_api(mocked, monkeypatch, capsys):
    monkeypatch.setenv("EGD_CLIENT_ID", "id")
    monkeypatch.setenv("EGD_CLIENT_SECRET", "secret")
    mocked.add(responses.POST, PROD_TOKEN_URL, json={"access_token": "t"})
    ts = [f"2024-01-01T{h:02d}:{m:02d}:00.000Z" for h in range(23, 24) for m in (0, 15, 30, 45)]
    ts += [f"2024-01-01T{h:02d}:{m:02d}:00.000Z" for h in range(0, 23) for m in (0, 15, 30, 45)]
    mocked.add(
        responses.GET,
        f"{PROD_DATA_URL}/spotreby",
        json=[
            {
                "profile": "ICC1",
                "units": "KW",
                "data": [{"timestamp": t, "value": 1.0, "status": "IU012"} for t in ts],
            }
        ],
    )
    add_ote(mocked, FakeOte(lambda d, i: 4000.0))
    rc = main(["calc", "--ean", "859", "--from", "2024-01-01", "--to", "2024-01-01", "--json"])
    assert rc == 0
    s = json.loads(capsys.readouterr().out)["summary"]
    # 23:00Z on 2024-01-01 belongs to 2 Jan local, so 92 of 96 quarters count: 92 × 0.25 kWh
    assert s["kwh"] == pytest.approx(23.0)
    assert s["total_czk"] == pytest.approx(23.0 * 4.0)


def test_prices_command(mocked, tmp_path, capsys):
    add_ote(mocked, FakeOte(lambda d, i: float(i)))
    out = tmp_path / "p.csv"
    assert main(["prices", "--from", "2024-03-31", "--to", "2024-03-31", "-o", str(out)]) == 0
    assert "23 periods" in capsys.readouterr().out
    assert len(out.read_text().splitlines()) == 24


def test_fetch_command(mocked, monkeypatch, tmp_path):
    monkeypatch.setenv("EGD_CLIENT_ID", "id")
    monkeypatch.setenv("EGD_CLIENT_SECRET", "secret")
    mocked.add(responses.POST, PROD_TOKEN_URL, json={"access_token": "t"})
    mocked.add(
        responses.GET,
        f"{PROD_DATA_URL}/spotreby",
        json=[{"units": "KWH", "data": [{"timestamp": "2024-01-01T00:00:00Z", "value": 0.5}]}],
    )
    out = tmp_path / "f.csv"
    assert (
        main(
            ["fetch", "--ean", "859", "--from", "2024-01-01", "--to", "2024-01-01", "-o", str(out)]
        )
        == 0
    )
    assert out.read_text().splitlines()[1] == "2024-01-01T01:00:00+01:00,0.5,"


def test_bad_period(capsys, tmp_path):
    assert main(["prices", "--from", "2025-02-01", "--to", "2025-01-01"]) == 2
    assert "before" in capsys.readouterr().err
