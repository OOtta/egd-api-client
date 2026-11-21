"""egd-api-client: client library and CLI for the EG.D Distribuce24 OpenAPI.

Library use::

    from egd_api_client import EgdClient, EgdCredentials

    client = EgdClient(make_session(), EgdCredentials(client_id, client_secret))
    client.metering_points()
    client.readings(ean, start_utc, end_utc)

The :mod:`egd_api_client.spot` subpackage prices consumption with OTE day-ahead prices.
"""

from egd_api_client._version import __version__
from egd_api_client.client import EgdClient, EgdCredentials, EgdRequestError
from egd_api_client.http import FetchError, make_session
from egd_api_client.models import LoadResult, Reading

__all__ = [
    "EgdClient",
    "EgdCredentials",
    "EgdRequestError",
    "FetchError",
    "LoadResult",
    "Reading",
    "__version__",
    "make_session",
]
