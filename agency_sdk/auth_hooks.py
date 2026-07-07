"""Per-request bearer-token auth hooks, shared across the SDK.

A static ``Authorization`` header set at client construction goes stale: the
Keycloak m2m JWT rotates, and long-running clients (OTLP exporters, the gateway
``openai`` client) freeze whatever was set at build time. Both ``requests`` and
``httpx`` instead invoke their ``auth`` callable on *every* request, so routing
the token through these hooks means each request re-reads the (auto-refreshing,
cached) token and never sends an expired one.

Dependency-light on purpose so any consumer can reuse it: only core ``requests``
at import time; ``httpx`` is imported lazily inside :func:`make_httpx_bearer_auth`
(it arrives with the ``[observability]`` or ``[openai]`` extras).
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
