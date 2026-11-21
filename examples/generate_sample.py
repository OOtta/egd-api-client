"""Generate a synthetic household consumption file in a Czech portal-like CSV format.

Usage: python examples/generate_sample.py 2025-10-01 2025-11-30 > examples/sample_consumption.csv

Columns: "Datum a čas" (local time, END of the 15-minute interval, dd.mm.yyyy HH:MM),
"Činná spotřeba (kWh)" with decimal comma, ";" separator. Deterministic (seeded).
"""

from __future__ import annotations

import math
import random
import sys
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

PRAGUE = ZoneInfo("Europe/Prague")


def main(start: str, end: str) -> None:
    rng = random.Random(42)
    t = datetime.combine(date.fromisoformat(start), datetime.min.time(), PRAGUE).astimezone(UTC)
    stop = datetime.combine(
        date.fromisoformat(end) + timedelta(days=1), datetime.min.time(), PRAGUE
    ).astimezone(UTC)
    out = sys.stdout
    out.write("Datum a čas;Činná spotřeba (kWh);Status\n")
    while t < stop:
        local = t.astimezone(PRAGUE)
        h = local.hour + local.minute / 60
        base = 0.06  # fridge, standby (~240 W)
        morning = 0.25 * math.exp(-((h - 7.0) ** 2) / 1.5)
        evening = 0.40 * math.exp(-((h - 19.0) ** 2) / 4.0)
        weekend = 0.08 if local.weekday() >= 5 and 9 <= h <= 15 else 0.0
        kwh = max(0.0, base + morning + evening + weekend + rng.gauss(0, 0.02))
        label = (t + timedelta(minutes=15)).astimezone(PRAGUE)
        value = f"{kwh:.3f}".replace(".", ",")
        out.write(f"{label:%d.%m.%Y %H:%M};{value};W\n")
        t += timedelta(minutes=15)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
