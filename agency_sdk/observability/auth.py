"""Per-request bearer-token auth hooks for the observability exporters.

OTLP HTTP exporters freeze their headers into a ``requests.Session`` at
construction, so a token placed there would never refresh. ``requests`` instead
invokes ``session.auth`` on *every* request, so routing the token through
:class:`BearerTokenAuth` means each export re-reads the (auto-refreshing, cached)
token and a long-running process never sends an expired one.

:func:`make_httpx_bearer_auth` provides the mirror hook for the Langfuse client
(httpx). httpx is imported lazily inside the factory so this module loads without
it (it arrives only with the ``[observability]`` extra).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

import requests.auth

if TYPE_CHECKING:
    import httpx

# A callable returning a fresh bearer token, or None when unavailable.
TokenSupplier = Callable[[], "str | None"]


class BearerTokenAuth(requests.auth.AuthBase):
    """requests per-request auth that stamps a fresh bearer token on every call."""

    def __init__(self, token_supplier: TokenSupplier) -> None:
        self._token_supplier = token_supplier

    def __call__(self, request: Any) -> Any:
        token = self._token_supplier()
        if token:
            request.headers["Authorization"] = f"Bearer {token}"
        return request


def make_httpx_bearer_auth(token_supplier: TokenSupplier) -> "httpx.Auth":
    """Build an ``httpx.Auth`` mirroring :class:`BearerTokenAuth` (lazy httpx import)."""
    import httpx

    class _HttpxBearerAuth(httpx.Auth):
        def __init__(self, supplier: TokenSupplier) -> None:
            self._token_supplier = supplier

        def auth_flow(self, request: Any) -> Any:
            token = self._token_supplier()
            if token:
                request.headers["Authorization"] = f"Bearer {token}"
            yield request

    return _HttpxBearerAuth(token_supplier)
