"""Reading consumption data from files and writing the canonical CSV format.

Supported inputs:

* the tool's own canonical CSV (written by ``egd-spot fetch``):
  ``start,kwh,status`` with ISO-8601 timestamps including UTC offset;
* generic CSV exported from a portal or spreadsheet (``;`` / ``,`` / tab separated,
  decimal comma or point, Czech or ISO date formats);
* XLSX (the Distribuce24 portal exports ``.xlsx``) – needs the ``xlsx`` extra (openpyxl).

Column detection can be overridden with ``time_column`` / ``value_column``.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal

from egd_spot.models import LoadResult, Reading
from egd_spot.timeutil import PRAGUE, QUARTER, to_local

Unit = Literal["auto", "kwh", "kw"]
Convention = Literal["auto", "start", "end"]

TIME_HEADER = re.compile(r"(datum|date|čas|cas|time|timestamp|start|od\b)", re.IGNORECASE)
TIME_ONLY_HEADER = re.compile(r"^(čas|cas|time|hodina)\b", re.IGNORECASE)
DATE_ONLY_HEADER = re.compile(r"^(datum|date|den|day)$", re.IGNORECASE)
CONSUMPTION_HINT = re.compile(
    r"(icc1|icq2|spotřeb|spotreb|odběr|odber|consumption|import|kwh)", re.I
)
EXPORT_HINT = re.compile(r"(isc1|isq2|dodávk|dodavk|přetok|pretok|export|výrob|vyrob)", re.I)

DATE_FORMATS = (
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d. %m. %Y %H:%M:%S",
    "%d. %m. %Y %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M",
)


class InputFormatError(ValueError):
    pass


@dataclass(slots=True)
class FileOptions:
    time_column: str | None = None
    value_column: str | None = None
    unit: Unit = "auto"
    timestamps: Convention = "auto"
    interval_minutes: int | None = None  # None = infer from data
    sheet: str | None = None


# ---------------------------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------------------------
def load_file(path: Path, options: FileOptions | None = None) -> LoadResult:
    options = options or FileOptions()
    suffix = path.suffix.lower()
    rows = _read_xlsx(path, options.sheet) if suffix in (".xlsx", ".xlsm") else _read_csv(path)
    return parse_rows(rows, options)


def write_canonical_csv(readings: Iterable[Reading], path: Path | io.TextIOBase) -> None:
    """Write readings as ``start,kwh,status`` (start = local time with offset)."""

    def _write(fh: Any) -> None:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["start", "kwh", "status"])
        for r in readings:
            w.writerow([to_local(r.start).isoformat(), f"{r.kwh:.6g}", r.status or ""])

    if isinstance(path, Path):
        with path.open("w", encoding="utf-8", newline="") as fh:
            _write(fh)
    else:
        _write(path)


# ---------------------------------------------------------------------------------------------
# readers
# ---------------------------------------------------------------------------------------------
def _read_csv(path: Path) -> list[list[Any]]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1250"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover
        raise InputFormatError(f"{path}: unknown text encoding")
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    return [row for row in csv.reader(io.StringIO(text), delimiter=delimiter) if any(row)]


def _read_xlsx(path: Path, sheet: str | None) -> list[list[Any]]:
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise InputFormatError(
            "Reading .xlsx needs openpyxl: pipx install 'egd-spot-calc[xlsx]' "
            "(or pip install openpyxl). Alternatively save the sheet as CSV."
        ) from exc
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    wb.close()
    return [r for r in rows if any(c not in (None, "") for c in r)]


# ---------------------------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------------------------
def _find_header(rows: Sequence[Sequence[Any]]) -> int:
    for i, row in enumerate(rows[:50]):
        cells = [str(c).strip() for c in row if c not in (None, "")]
        # a header row has a date/time-like column name and is mostly non-numeric
        if (
            len(cells) >= 2
            and any(TIME_HEADER.search(c) for c in cells)
            and sum(_looks_number(c) for c in cells) <= len(cells) // 2
        ):
            return i
    raise InputFormatError(
        "Could not find a header row with a date/time column. Use --time-column/--value-column."
    )


def _looks_number(v: Any) -> bool:
    if isinstance(v, int | float):
        return True
    try:
        float(str(v).replace(",", ".").replace("\xa0", "").replace(" ", ""))
    except ValueError:
        return False
    return True


def _col_index(header: Sequence[str], wanted: str) -> int:
    if wanted.isdigit():
        return int(wanted)
    for i, h in enumerate(header):
        if h.strip().lower() == wanted.strip().lower():
            return i
    for i, h in enumerate(header):
        if wanted.strip().lower() in h.lower():
            return i
    raise InputFormatError(f"Column {wanted!r} not found; available: {list(header)}")


def _detect_columns(header: Sequence[str]) -> tuple[int, int | None, int]:
    """Return (datetime-or-date column, separate time column or None, value column)."""
    time_col = date_col = None
    for i, h in enumerate(header):
        if date_col is None and DATE_ONLY_HEADER.match(h.strip()):
            date_col = i
        elif time_col is None and TIME_ONLY_HEADER.match(h.strip()):
            time_col = i
    if date_col is not None and time_col is not None:
        main, extra = date_col, time_col
    else:
        main = next((i for i, h in enumerate(header) if TIME_HEADER.search(h)), 0)
        extra = None
    taken = {main, extra}
    candidates = [i for i, h in enumerate(header) if i not in taken]
    value = next(
        (
            i
            for i in candidates
            if CONSUMPTION_HINT.search(header[i]) and not EXPORT_HINT.search(header[i])
        ),
        None,
    )
    if value is None:
        value = next((i for i in candidates if not EXPORT_HINT.search(header[i])), None)
    if value is None:
        raise InputFormatError("Could not detect the consumption value column; use --value-column.")
    return main, extra, value


def _parse_naive_or_aware(v: Any, time_part: Any = None) -> datetime:
    if isinstance(v, datetime):
        dt = v
    elif isinstance(v, date):
        dt = datetime.combine(v, time())
    else:
        s = str(v).strip()
        dt = None
        plus_day = False
        if time_part is not None:
            s = f"{s} {_time_str(time_part)}"
        if re.search(r"\b24:00(:00)?$", s):
            s = re.sub(r"\b24:00(:00)?$", "00:00", s)
            plus_day = True
        try:
            iso = s[:-1] + "+00:00" if s.endswith("Z") else s
            dt = datetime.fromisoformat(iso)
        except ValueError:
            for fmt in DATE_FORMATS:
                try:
                    dt = datetime.strptime(s, fmt)
                    break
                except ValueError:
                    continue
        if dt is None:
            raise InputFormatError(f"Unrecognised date/time value: {v!r}")
        if plus_day:
            dt += timedelta(days=1)
        return dt
    if time_part is not None and not isinstance(v, str):
        t = _parse_time(time_part)
        dt = datetime.combine(dt.date(), t)
    return dt


def _time_str(v: Any) -> str:
    if isinstance(v, time):
        return v.strftime("%H:%M:%S")
    if isinstance(v, datetime):
        return v.strftime("%H:%M:%S")
    return str(v).strip()


def _parse_time(v: Any) -> time:
    if isinstance(v, time):
        return v
    if isinstance(v, datetime):
        return v.time()
    parts = [int(p) for p in str(v).strip().split(":")]
    return time(*parts[:3]) if parts[0] < 24 else time(0)


def _parse_value(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, int | float):
        return float(v)
    s = str(v).strip().replace("\xa0", "").replace(" ", "")
    if not s:
        return None
    return float(s.replace(",", "."))


def _localize_sequence(naive: list[datetime]) -> list[datetime]:
    """Convert naive local timestamps to UTC, resolving the duplicate hour at the end of DST
    by the order of the rows (first occurrence = summer time)."""
    out: list[datetime] = []
    prev: datetime | None = None
    for dt in naive:
        cands = sorted({dt.replace(tzinfo=PRAGUE, fold=f).astimezone(UTC) for f in (0, 1)})
        choice = cands[0]
        if prev is not None and len(cands) > 1 and cands[0] <= prev:
            choice = cands[1]
        out.append(choice)
        prev = choice
    return out


def parse_rows(rows: Sequence[Sequence[Any]], options: FileOptions) -> LoadResult:
    if not rows:
        raise InputFormatError("Input file is empty")
    hdr_idx = _find_header(rows)
    header = [str(c).strip() if c is not None else "" for c in rows[hdr_idx]]
    main, extra, value = _detect_columns(header)
    if options.time_column:
        main, extra = _col_index(header, options.time_column), None
    if options.value_column:
        value = _col_index(header, options.value_column)

    unit = options.unit
    if unit == "auto":
        h = header[value].lower()
        if re.search(r"\bkwh\b|\(kwh\)|\[kwh\]", h) or "icq" in h:
            unit = "kwh"
        elif re.search(r"\bkw\b|\(kw\)|\[kw\]", h) or "icc1" in h:
            unit = "kw"
        else:
            unit = "kwh"

    stamps: list[datetime] = []
    values: list[float] = []
    statuses: list[str | None] = []
    status_col = next((i for i, h in enumerate(header) if h.lower() in ("status", "stav")), None)
    for row in rows[hdr_idx + 1 :]:
        if main >= len(row) or value >= len(row) or row[main] in (None, ""):
            continue
        val = _parse_value(row[value])
        if val is None:
            continue
        stamps.append(_parse_naive_or_aware(row[main], row[extra] if extra is not None else None))
        values.append(val)
        statuses.append(
            str(row[status_col]) if status_col is not None and status_col < len(row) else None
        )
    if not stamps:
        raise InputFormatError("No data rows found")

    warnings: list[str] = []
    if all(s.tzinfo is not None for s in stamps):
        utc = [s.astimezone(UTC) for s in stamps]
        local_first = to_local(utc[0])
    else:
        utc = _localize_sequence([s.replace(tzinfo=None) for s in stamps])
        local_first = stamps[0]

    # interval length
    if options.interval_minutes:
        length = timedelta(minutes=options.interval_minutes)
    else:
        diffs = sorted(b - a for a, b in pairwise(utc) if b > a)
        length = diffs[len(diffs) // 2] if diffs else QUARTER
        if length not in (QUARTER, timedelta(hours=1)):
            warnings.append(f"Unusual interval length {length}; check the input.")

    convention = options.timestamps
    if convention == "auto" and [h.lower() for h in header[:2]] == ["start", "kwh"]:
        convention = "start"  # our own canonical format
    if convention == "auto":
        if (local_first.hour, local_first.minute) == (0, 0):
            convention = "start"
        elif local_first.hour == 0 and local_first.minute == length.seconds // 60:
            convention = "end"
        else:
            convention = "end"
            warnings.append(
                "Could not infer whether timestamps mark interval start or end; assuming END "
                "(common in Czech metering exports). Override with --timestamps."
            )

    hours = length.total_seconds() / 3600
    readings = []
    for ts, val, st in zip(utc, values, statuses, strict=True):
        start = ts - length if convention == "end" else ts
        kwh = val * hours if unit == "kw" else val
        readings.append(Reading(start, kwh, length, st))
    readings.sort(key=lambda r: r.start)
    return LoadResult(readings, warnings)
