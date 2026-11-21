"""Shared HTTP session."""

from __future__ import annotations

import requests

from egd_spot import __version__

USER_AGENT = f"egd-spot-calc/{__version__} (+https://github.com/OWNER/egd-spot-calc)"
DEFAULT_TIMEOUT = 60


class FetchError(RuntimeError):
    """A remote data source could not be read."""


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s
