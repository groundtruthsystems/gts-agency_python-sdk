"""Backward-compatible re-export of the shared bearer-token auth hooks.

The canonical implementations now live in :mod:`agency_sdk.auth_hooks` — a core,
dependency-light module — so non-observability consumers (e.g. the gateway
client's ``openai`` helpers) can reuse them without importing this optional
submodule. This module is a thin re-export because the OTLP ``requests``
exporter (``session.auth = BearerTokenAuth(...)``) and the Langfuse ``httpx``
client (``make_httpx_bearer_auth(...)``) reference this path.
"""

from __future__ import annotations

from agency_sdk.auth_hooks import BearerTokenAuth, TokenSupplier, make_httpx_bearer_auth

__all__ = ["BearerTokenAuth", "TokenSupplier", "make_httpx_bearer_auth"]
