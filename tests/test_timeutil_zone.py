from zoneinfo import ZoneInfoNotFoundError

import pytest

from egd_api_client import timeutil


def test_load_zone_ok():
    assert str(timeutil.load_zone("Europe/Prague")) == "Europe/Prague"


def test_load_zone_missing_db_has_hint(monkeypatch):
    def boom(key):
        raise ZoneInfoNotFoundError(f"No time zone found with key {key}")

    monkeypatch.setattr(timeutil, "ZoneInfo", boom)
    with pytest.raises(ZoneInfoNotFoundError, match="tzdata"):
        timeutil.load_zone("Europe/Prague")
