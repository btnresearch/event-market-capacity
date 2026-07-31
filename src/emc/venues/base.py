"""Read-only venue adapter contract.

Two hard rules hold for every adapter in this package, and
``tests/test_readonly_guardrails.py`` fails the build if either is broken:

* HTTP GET only. No POST, PUT, PATCH, or DELETE.
* No credentials. No API keys, no signatures, no session tokens, no auth headers.

Adapters are split into a pure parser and a thin fetcher. The parser turns a
decoded public payload into :class:`~emc.models.MarketSnapshot` objects and has
no I/O, which is what lets the ingest layer be tested against recorded payload
shapes in an environment with no network access to the venues.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from emc.models import MarketRef, MarketSnapshot

__all__ = ["FetchBlocked", "PayloadShapeError", "VenueClient", "get_json"]

_ALLOWED_METHOD = "GET"


class FetchBlocked(RuntimeError):
    """Raised when a public endpoint cannot be reached.

    Distinguished from a parse failure on purpose. An unreachable endpoint means
    the probe has no data and must report that, rather than reporting zero
    capacity, which would read as "we looked and found nothing".
    """


class PayloadShapeError(ValueError):
    """Raised when a payload is reachable but not in the expected shape.

    Also kept distinct from ``FetchBlocked``: a shape change means the adapter is
    stale and its output cannot be trusted, which is a different problem from
    being offline.
    """


@runtime_checkable
class VenueClient(Protocol):
    """Minimal read-only surface. Deliberately has no write methods."""

    venue: str

    def list_sports_markets(self, limit: int = 100) -> Sequence[MarketRef]:
        """Public market discovery."""
        ...

    def fetch_snapshot(self, market_id: str) -> MarketSnapshot:
        """Public order book for one market, stamped with capture time."""
        ...


def get_json(url: str, params: dict[str, Any] | None = None, timeout: float = 10.0) -> Any:
    """Unauthenticated GET returning decoded JSON.

    Sends no credentials and accepts no request body. ``httpx`` is imported lazily
    so the offline fixture path and the whole test suite work without it
    installed.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise FetchBlocked(
            "httpx is not installed; install the 'live' extra to fetch public data"
        ) from exc

    try:
        response = httpx.request(
            _ALLOWED_METHOD,
            url,
            params=params,
            timeout=timeout,
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:  # noqa: BLE001 - deliberately uniform for callers
        raise FetchBlocked(f"GET {url} failed: {type(exc).__name__}: {exc}") from exc
