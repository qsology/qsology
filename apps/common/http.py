"""HTTP client for talking to external services

Callers take a fetch function so tests can swap it out without touching the
network"""

from collections.abc import Callable, Mapping

import requests
from django.conf import settings

Fetch = Callable[..., bytes]


class HTTPError(Exception):
    """A request to an external service failed"""


# Shared so connections to the same host are reused
session = requests.Session()


def get(
    url: str,
    params: Mapping[str, str] | None = None,
    timeout: float = 30,
) -> bytes:
    """GET a URL and return the body

    Raises HTTPError if the request fails or the response is an error"""
    try:
        response = session.get(
            url,
            params=params,
            headers={"User-Agent": settings.HTTP_USER_AGENT},
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPError(f"Request to {url} failed: {exc}") from exc
    return response.content
