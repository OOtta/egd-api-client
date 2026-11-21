import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from egd_spot.consumption_io import (
    FileOptions,
    InputFormatError,
    load_file,
    parse_rows,
    write_canonical_csv,
)
from egd_spot.models import Reading


def write(tmp_path: Path, text: str, name: str = "c.csv", encoding: str = "utf-8") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding=encoding)
    return p


def test_czech_csv_end_labelled_with_dst_duplicates(tmp_path):
    rows = ["Datum a čas;Činná spotřeba (kWh);Status"]
    labels = [
        "01:45",
        "02:00",
        "02:15",
        "02:30",
        "02:45",
        "02:00",
        "02:15",
        "02:30",
        "02:45",
        "03:00",
    ]
    rows += [f"26.10.2025 {t};0,1;W" for t in labels]
    res = load_file(write(tmp_path, "\n".join(rows)))
    starts = [r.start for r in res.readings]
    assert starts[0] == datetime(2025, 10, 25, 23, 30, tzinfo=UTC)  # 01:30-01:45 CEST
    assert starts == [starts[0] + i * timedelta(minutes=15) for i in range(10)]
    assert sum(r.kwh for r in res.readings) == pytest.approx(1.0)
    # first label is not 00:00 / 00:15 → warning about assumed convention
    assert any("END" in w for w in res.warnings)


def test_auto_detects_start_convention_and_kw_unit(tmp_path):
    text = "Datum;Výkon ICC1 (kW)\n01.11.2025 00:00;4\n01.11.2025 00:15;2\n"
    res = load_file(write(tmp_path, text))
    assert res.readings[0].start == datetime(2025, 10, 31, 23, 0, tzinfo=UTC)
    assert [r.kwh for r in res.readings] == [1.0, 0.5]
    assert res.warnings == []


def test_separate_date_and_time_columns_comma_csv(tmp_path):
    text = (
        "Date,Time,Consumption kWh,Export kWh\n2025-11-01,00:15,0.2,0.0\n2025-11-01,00:30,0.3,0.0\n"
    )
    res = load_file(write(tmp_path, text))
    assert [r.kwh for r in res.readings] == [0.2, 0.3]
    assert res.readings[0].start == datetime(2025, 10, 31, 23, 0, tzinfo=UTC)


def test_24_00_label_and_cp1250(tmp_path):
    text = "Datum;Spotřeba [kWh]\n01.11.2025 23:45;1\n01.11.2025 24:00;2\n"
    res = load_file(write(tmp_path, text, encoding="cp1250"), FileOptions(timestamps="end"))
    assert res.readings[-1].start == datetime(2025, 11, 1, 22, 45, tzinfo=UTC)


def test_column_overrides_and_preamble(tmp_path):
    text = (
        "EAN: 8591824000000\n\nčas;a;b\n"
        "2025-11-01T00:00:00+01:00;9;0.5\n2025-11-01T00:15:00+01:00;9;0.5\n"
    )
    res = load_file(write(tmp_path, text), FileOptions(value_column="b", timestamps="start"))
    assert [r.kwh for r in res.readings] == [0.5, 0.5]


def test_missing_header_raises():
    with pytest.raises(InputFormatError):
        parse_rows([["1", "2"], ["3", "4"]], FileOptions())


def test_canonical_roundtrip(tmp_path):
    t0 = datetime(2025, 3, 30, 0, 45, tzinfo=UTC)
    readings = [Reading(t0 + i * timedelta(minutes=15), 0.125 * i, status="W") for i in range(6)]
    buf = io.StringIO()
    write_canonical_csv(readings, buf)
    assert buf.getvalue().splitlines()[1] == "2025-03-30T01:45:00+01:00,0,W"
    res = load_file(write(tmp_path, buf.getvalue()))
    assert [r.start for r in res.readings] == [r.start for r in readings]
    assert [r.kwh for r in res.readings] == [r.kwh for r in readings]


def test_hourly_input_detected(tmp_path):
    text = "Datum;kWh\n01.11.2025 01:00;1\n01.11.2025 02:00;1\n01.11.2025 03:00;1\n"
    res = load_file(write(tmp_path, text), FileOptions(timestamps="end"))
    assert res.readings[0].length == timedelta(hours=1)


def test_xlsx(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Historie spotřeb"])
    ws.append(["Datum a čas", "Činná spotřeba (kWh)"])
    ws.append([datetime(2025, 11, 1, 0, 15), 0.25])
    ws.append([datetime(2025, 11, 1, 0, 30), 0.5])
    p = tmp_path / "export.xlsx"
    wb.save(p)
    res = load_file(p)
    assert [r.kwh for r in res.readings] == [0.25, 0.5]
    assert res.readings[0].start == datetime(2025, 10, 31, 23, 0, tzinfo=UTC)
