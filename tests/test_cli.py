import io
import json
import sys
from datetime import date

import pytest
import responses

from conftest import FakeOte, add_cnb, add_ote
from egd_api_client.cli import main
from egd_api_client.cli_common import parse_date_arg
from egd_api_client.client import PROD_DATA_URL, PROD_TOKEN_URL


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("EGD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    for v in ("EGD_CLIENT_ID", "EGD_CLIENT_SECRET", "EGD_EAN", "EGD_CONFIG"):
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
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def test_calc_from_file_json_and_exports(mocked, tmp_path, capsys):
    add_cnb(mocked)
    add_ote(mocked, FakeOte(lambda d, i: 2000.0 if d < date(2025, 10, 1) else 100.0))
    out_json = tmp_path / "r.json"
    out_csv = tmp_path / "i.csv"
    rc = main(
        [
            "spot",
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
    assert json.loads(out_json.read_text(encoding="utf-8"))["summary"]["kwh"] == pytest.approx(48)
    assert len(out_csv.read_text(encoding="utf-8").splitlines()) == 1 + 192


def test_calc_text_output(mocked, tmp_path, capsys):
    add_cnb(mocked)
    add_ote(mocked, FakeOte(lambda d, i: 1000.0 if d < date(2025, 10, 1) else 40.0))
    assert (
        main(["spot", "calc", "-i", str(sample_csv(tmp_path)), "-b", "month", "--fixed-price", "9"])
        == 0
    )
    out = capsys.readouterr().out
    assert "TOTAL:" in out
    assert "CHEAPER" in out
    assert "2025-09" in out and "2025-10" in out


def test_calc_api_requires_credentials(capsys):
    rc = main(["spot", "calc", "--ean", "859", "--from", "2025-01"])
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
    rc = main(
        ["spot", "calc", "--ean", "859", "--from", "2024-01-01", "--to", "2024-01-01", "--json"]
    )
    assert rc == 0
    s = json.loads(capsys.readouterr().out)["summary"]
    # 23:00Z on 2024-01-01 belongs to 2 Jan local, so 92 of 96 quarters count: 92 × 0.25 kWh
    assert s["kwh"] == pytest.approx(23.0)
    assert s["total_czk"] == pytest.approx(23.0 * 4.0)


def test_prices_command(mocked, tmp_path, capsys):
    add_ote(mocked, FakeOte(lambda d, i: float(i)))
    out = tmp_path / "p.csv"
    assert (
        main(
            [
                "spot",
                "prices",
                "--currency",
                "czk",
                "--from",
                "2024-03-31",
                "--to",
                "2024-03-31",
                "-o",
                str(out),
            ]
        )
        == 0
    )
    assert "23 periods" in capsys.readouterr().out
    assert len(out.read_text(encoding="utf-8").splitlines()) == 24


def test_data_command(mocked, monkeypatch, tmp_path):
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
        main(["data", "--ean", "859", "--from", "2024-01-01", "--to", "2024-01-01", "-o", str(out)])
        == 0
    )
    assert out.read_text(encoding="utf-8").splitlines()[1] == "2024-01-01T01:00:00+01:00,0.5,"


def test_bad_period(capsys, tmp_path):
    assert (
        main(["spot", "prices", "--currency", "czk", "--from", "2025-02-01", "--to", "2025-01-01"])
        == 2
    )
    assert "before" in capsys.readouterr().err


def test_data_production_json(mocked, monkeypatch, tmp_path):
    monkeypatch.setenv("EGD_CLIENT_ID", "id")
    monkeypatch.setenv("EGD_CLIENT_SECRET", "secret")
    mocked.add(responses.POST, PROD_TOKEN_URL, json={"access_token": "t"})
    mocked.add(responses.GET, f"{PROD_DATA_URL}/om", json=[{"ean": "859", "typMereni": "B"}])
    mocked.add(
        responses.GET,
        f"{PROD_DATA_URL}/spotreby",
        json={"units": "kWh", "data": [{"timestamp": "2024-01-01T00:00:00Z", "value": 0.25}]},
    )
    out = tmp_path / "p.json"
    argv = ["data", "--ean", "859", "--kind", "production", "--from", "2024-01-01"]
    assert main([*argv, "--to", "2024-01-01", "-o", str(out)]) == 0
    call = next(c for c in mocked.calls if "/spotreby" in c.request.url)
    assert "profile=ISC1" in call.request.url
    rows = json.loads(out.read_text(encoding="utf-8"))
    assert rows == [{"start": "2024-01-01T01:00:00+01:00", "kwh": 0.25, "status": None}]


def test_spot_fx_command(capsys, mocked):
    add_cnb(mocked)
    assert main(["spot", "fx", "--from", "2025-09-27", "--to", "2025-10-28", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["days"] == 32 and out["converted_days"] == 28
    rows = {r["day"]: r for r in out["days_detail"]}
    assert rows["2025-09-27"]["eur_czk"] is None  # hourly era: OTE CZK prices used directly
    assert rows["2025-10-04"] == {
        "day": "2025-10-04",
        "eur_czk": pytest.approx(24.27),
        "fixing_date": "2025-10-03",
        "note": "no fixing that day → last fixing before",
    }
    assert rows["2025-10-28"]["fixing_date"] == "2025-10-27"
    assert rows["2025-10-27"]["fixing_date"] == "2025-10-27"


def test_prices_currency_is_required(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["spot", "prices", "--from", "2025-09-30"])
    assert exc.value.code == 2
    assert "--currency" in capsys.readouterr().err


def test_prices_eur_uses_ote_eur_values_in_both_eras(mocked, tmp_path, capsys):
    add_cnb(mocked)
    fake = FakeOte(lambda d, i: 2500.0 if d < date(2025, 10, 1) else 80.0)
    add_ote(mocked, fake)
    out = tmp_path / "p.csv"
    argv = ["spot", "prices", "--currency", "eur", "--from", "2025-09-30", "--to", "2025-10-01"]
    assert main([*argv, "--json", "-o", str(out)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["unit"] == "EUR/MWh"
    assert summary["min"] == pytest.approx(80.0) and summary["max"] == pytest.approx(100.0)
    rows = out.read_text(encoding="utf-8").splitlines()
    assert rows[0] == "start,minutes,eur_mwh,eur_czk"
    assert rows[1] == "2025-09-30T00:00:00+02:00,60,100.00,"  # OTE's own EUR (InEur=true)
    assert rows[-1].endswith(",15,80.00,24.295")
    assert ("GetDamPriceE", date(2025, 9, 30), date(2025, 9, 30)) in fake.calls


def test_prices_czk_converts_15min_era(mocked, tmp_path, capsys):
    add_cnb(mocked)
    add_ote(mocked, FakeOte(lambda d, i: 2500.0 if d < date(2025, 10, 1) else 80.0))
    out = tmp_path / "p.json"
    argv = ["spot", "prices", "--currency", "czk", "--from", "2025-09-30", "--to", "2025-10-01"]
    assert main([*argv, "-o", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data[0] == {
        "start": "2025-09-30T00:00:00+02:00",
        "minutes": 60,
        "czk_mwh": 2500.0,
        "eur_czk": None,
    }
    assert data[-1]["czk_mwh"] == pytest.approx(80 * 24.295)


def test_spot_fx_export(mocked, tmp_path, capsys):
    add_cnb(mocked)
    csv_out, json_out = tmp_path / "fx.csv", tmp_path / "fx.json"
    argv = ["spot", "fx", "--from", "2025-10-24", "--to", "2025-10-28"]
    assert main([*argv, "-o", str(csv_out)]) == 0
    text = capsys.readouterr().out
    assert "2025-10-28 Tue   24.325  2025-10-27" in text
    assert "2 day(s) used an earlier fixing" not in text  # Sat, Sun and the holiday → 3
    assert "3 day(s) used an earlier fixing" in text
    lines = csv_out.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "day,eur_czk,fixing_date,note"
    assert lines[1] == "2025-10-24,24.34,2025-10-24,"
    assert main([*argv, "-o", str(json_out)]) == 0
    assert json.loads(json_out.read_text(encoding="utf-8"))[4]["fixing_date"] == "2025-10-27"


def test_calc_fixed_vt_nt_with_manual_hours(mocked, tmp_path, capsys):
    add_cnb(mocked)
    add_ote(mocked, FakeOte(lambda d, i: 1000.0 if d < date(2025, 10, 1) else 40.0))
    argv = ["spot", "calc", "-i", str(sample_csv(tmp_path)), "--json"]
    argv += ["--fixed-price", "4", "--fixed-price-low", "2"]
    argv += ["--low-tariff-hours", "22:00-06:00", "--low-tariff-hours-weekend", "00:00-24:00"]
    assert main(argv) == 0
    data = json.loads(capsys.readouterr().out)
    # Tue 30.9. + Wed 1.10.: 8 NT hours a day → 2 × 8 kWh at 2 CZK, the rest at 4 CZK
    assert data["summary"]["low_tariff_kwh"] == pytest.approx(16)
    assert data["summary"]["fixed_total_czk"] == pytest.approx(16 * 2 + 32 * 4)
    assert "NT 00:00–06:00, 22:00–24:00" in data["low_tariff_schedule"]
    # weighted 160 CZK / 48 kWh; simple: 30.9. and 1.10. both 8 NT h of 24
    assert data["summary"]["avg_fixed_czk_mwh"] == pytest.approx(160 / 48 * 1000, abs=0.01)
    assert data["summary"]["avg_fixed_time_weighted_czk_mwh"] == pytest.approx(
        (8 * 2 + 16 * 4) / 24 * 1000, abs=0.01
    )


def test_calc_fixed_average_text_and_csv(mocked, tmp_path, capsys):
    add_cnb(mocked)
    add_ote(mocked, FakeOte(lambda d, i: 1000.0 if d < date(2025, 10, 1) else 40.0))
    out_csv = tmp_path / "b.csv"
    argv = ["spot", "calc", "-i", str(sample_csv(tmp_path)), "-b", "month", "--vat"]
    argv += ["--fixed-price", "4", "-o", str(out_csv)]
    assert main(argv) == 0
    text = capsys.readouterr().out
    assert "Avg fixed price:   4 000.00 CZK/MWh (weighted" in text
    assert "4 000.00 CZK/MWh (simple average" in text
    assert "incl. VAT 21 %:" not in text
    assert "VAT 21 %:" in text  # VAT total line stays
    assert "Fixed avg" in text
    assert "Spot+tariff" in text
    assert "(raw OTE, weighted by your consumption)" in text
    assert "Spot + tariff:" in text and "(simple average of the period)" in text
    assert "= " not in text.split("Spot + tariff:")[1].split("At negative")[0]
    rows = out_csv.read_text(encoding="utf-8").splitlines()
    assert "avg_fixed_czk_mwh" in rows[0].split(",")
    assert "avg_spot_tariff_czk_mwh" in rows[0].split(",")
    assert main(["spot", "calc", "-i", str(sample_csv(tmp_path))]) == 0
    assert "Avg fixed price" not in capsys.readouterr().out


def test_calc_nt_price_needs_schedule(capsys, tmp_path):
    argv = ["spot", "calc", "-i", str(sample_csv(tmp_path)), "--fixed-price", "4"]
    assert main([*argv, "--fixed-price-low", "2"]) == 2
    assert "--low-tariff-hours" in capsys.readouterr().err
    assert main(["spot", "calc", "-i", str(sample_csv(tmp_path)), "--fixed-price-low", "2"]) == 2


def test_calc_negative_coefficients(mocked, tmp_path, capsys):
    add_cnb(mocked)
    add_ote(mocked, FakeOte(lambda d, i: -100.0 if d < date(2025, 10, 1) else 40.0))
    argv = ["spot", "calc", "-i", str(sample_csv(tmp_path)), "--to", "2025-09-30", "--json"]
    argv += [
        "-m",
        "1.2",
        "-a",
        "400",
        "--negative-multiplier",
        "1",
        "--negative-add-czk-mwh",
        "500",
    ]
    assert main(argv) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["summary"]["energy_czk"] == pytest.approx(24 * (-100 + 500) / 1000)
    assert data["tariff"]["negative_multiplier"] == 1.0


def test_low_tariff_cli_errors(capsys, tmp_path):
    base = ["spot", "calc", "-i", str(sample_csv(tmp_path)), "--fixed-price", "4"]
    assert main([*base, "--fixed-price-low", "2", "--low-tariff-hours", "25:00-06:00"]) == 2
    assert "low-tariff hours: invalid time" in capsys.readouterr().err
    assert main([*base, "--low-tariff-hours-weekend", "00:00-24:00"]) == 2
    assert "needs --low-tariff-hours" in capsys.readouterr().err


def test_calc_distribution_text_and_json(mocked, tmp_path, capsys):
    add_cnb(mocked)
    add_ote(mocked, FakeOte(lambda d, i: 1000.0 if d < date(2025, 10, 1) else 40.0))
    base = ["spot", "calc", "-i", str(sample_csv(tmp_path)), "--to", "2025-09-30"]
    dist = ["--distribution-high", "2", "--distribution-low", "0.5",
            "--low-tariff-hours", "22:00-06:00", "--fixed-price", "4", "--vat"]  # fmt: skip
    assert main([*base, *dist, "-b", "month"]) == 0
    out = capsys.readouterr().out
    assert "Distribution:" in out and "Spot + distrib.:" in out and "Fixed + distrib.:" in out
    assert "Distrib." in out and "Total+D" in out and "Fixed+D" in out
    assert "NOT included" not in out
    assert main([*base, *dist, "--json"]) == 0
    s = json.loads(capsys.readouterr().out)["summary"]
    assert s["distribution_czk"] == pytest.approx(8 * 0.5 + 16 * 2)  # 24 kWh, 8 h NT
    assert s["total_with_distribution_czk"] == pytest.approx((24 * 1.0 + 36) * 1.21)
    assert s["fixed_total_with_distribution_czk"] == pytest.approx((24 * 4 + 36) * 1.21)


def test_calc_distribution_errors(capsys, tmp_path):
    base = ["spot", "calc", "-i", str(sample_csv(tmp_path))]
    assert main([*base, "--distribution-high", "2", "--distribution-low", "1"]) == 2
    assert "--distribution-low needs the low-tariff hours" in capsys.readouterr().err
    assert main([*base, "--distribution-low", "1", "--low-tariff-hours", "22:00-06:00"]) == 2
    assert "needs --distribution-high" in capsys.readouterr().err


def test_holidays_values(capsys, tmp_path, monkeypatch):
    base = ["spot", "calc", "-i", str(sample_csv(tmp_path)), "--low-tariff-hours", "22:00-06:00"]
    with pytest.raises(SystemExit):
        main([*base, "--holidays", "sunday"])
    assert "invalid choice" in capsys.readouterr().err
    cfg = tmp_path / "c.toml"
    cfg.write_text('[low_tariff]\nhours = "22:00-06:00"\nholidays = "sunday"\n', encoding="utf-8")
    monkeypatch.setenv("EGD_CONFIG", str(cfg))
    assert main(["spot", "calc", "-i", str(sample_csv(tmp_path))]) == 2
    assert "'weekday' or 'weekend'" in capsys.readouterr().err


def test_cp1252_stdout_gets_utf8(tmp_path, monkeypatch):
    """A Windows-style cp1252 stdout (redirected/piped) must not crash on 'ČNB'."""
    raw_out, raw_err = io.BytesIO(), io.BytesIO()
    out = io.TextIOWrapper(raw_out, encoding="cp1252", errors="strict")
    err = io.TextIOWrapper(raw_err, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    assert main(["spot", "calc", "-i", str(sample_csv(tmp_path)), "--to", "2025-09-30"]) == 0
    out.flush()
    assert out.encoding.lower().replace("-", "") == "utf8"
    assert "kWh" in raw_out.getvalue().decode("utf-8")


def test_utf8_stdout_left_alone(monkeypatch):
    from egd_api_client.cli_common import ensure_unicode_stdio

    stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)
    ensure_unicode_stdio()
    assert sys.stdout.errors == "strict"
