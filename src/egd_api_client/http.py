"""Shared HTTP session."""

from __future__ import annotations

import requests

from egd_api_client._version import __version__

USER_AGENT = f"egd-api-client/{__version__} (+https://github.com/OOtta/egd-api-client)"
DEFAULT_TIMEOUT = 60


class FetchError(RuntimeError):
    """A remote data source could not be read."""


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s
