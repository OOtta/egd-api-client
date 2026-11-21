"""Plain data containers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class Reading:
    """Energy consumed (or exported) in one metering interval."""

    start: datetime  # UTC, interval start
    kwh: float
    length: timedelta = timedelta(minutes=15)
    status: str | None = None

    @property
    def end(self) -> datetime:
        return self.start + self.length


@dataclass(frozen=True, slots=True)
class PricePoint:
    """Day-ahead market price for one delivery period."""

    start: datetime  # UTC
    length: timedelta
    czk_mwh: float
    eur_mwh: float | None = None
    eur_czk: float | None = None  # exchange rate used (None when OTE published CZK directly)

    @property
    def end(self) -> datetime:
        return self.start + self.length


@dataclass(slots=True)
class LoadResult:
    readings: list[Reading]
    warnings: list[str] = field(default_factory=list)
    available_from: datetime | None = None  # set when the source has no data before this
