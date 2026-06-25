"""Shared base for the per-domain delegate clients.

Centralizes credential / base-URL storage and the common authenticated-request
plumbing (bearer header, optional JSON content type, 30s timeout,
``raise_for_status``) so each delegate only sets its ``api_path`` and declares its
typed methods.

- Most delegates use :meth:`_make_request`, which parses and returns the JSON body.
- Delegates that need the raw response (e.g. ontology export, which returns
  non-JSON text) call :meth:`_request` with ``json_content_type=False``.
"""

from __future__ import annotations

from typing import Any

import requests

from agency_sdk.credentials import CredentialsSupplier


class BaseDelegateClient:
    #: API path prefix appended to ``base_url`` (e.g. ``"/api/datasets"``). Set by subclasses.
    api_path: str = ""

    def __init__(self, token_supplier: CredentialsSupplier, base_url: str = "http://localhost:9003"):
        self.base_url = base_url.rstrip("/")
        self.token_supplier = token_supplier

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        data: Any = None,
        params: dict[str, Any] | None = None,
        json_content_type: bool = True,
    ) -> requests.Response:
        """Issue an authenticated request, raise on HTTP error, and return the raw response."""
        headers = {"Authorization": f"Bearer {self.token_supplier.bearer_token()}"}
        if json_content_type:
            headers["Content-Type"] = "application/json"
        response = requests.request(
            method=method,
            url=f"{self.base_url}{self.api_path}{endpoint}",
            headers=headers,
            json=data,
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        return response

    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Any = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Issue a JSON request and return the parsed dict body (``{}`` when empty)."""
        response = self._request(method, endpoint, data=data, params=params)
        result: dict[str, Any] = response.json() if response.content else {}
        return result
