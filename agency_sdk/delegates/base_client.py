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

import time
from typing import Any

import requests

from agency_sdk.credentials import CredentialsSupplier

#: Backoff waits between transport retries; ``len`` + 1 = total attempts (here: 3).
_TRANSPORT_RETRY_WAITS: tuple[float, ...] = (1.0, 2.0)

#: Methods whose connection failures are safe to retry automatically. Only pure
#: reads qualify: a ``ConnectionError`` can be a reset AFTER the server committed
#: (``RemoteDisconnected``), so retrying a write would double-apply a POST or hit a
#: now-404 on a DELETE. Writers that are genuinely idempotent opt in per call
#: (``retry=True``) — never by default.
_AUTO_RETRY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


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
        retry: bool | None = None,
    ) -> requests.Response:
        """Issue an authenticated request, raise on HTTP error, and return the raw response.

        Args:
            retry: Override connection-retry behaviour. ``None`` (default) auto-retries
                only pure reads (:data:`_AUTO_RETRY_METHODS`). Pass ``True`` to retry a
                write that is genuinely safe to repeat (e.g. an idempotent update),
                ``False`` to force a single attempt.
        """
        headers = {"Authorization": f"Bearer {self.token_supplier.bearer_token()}"}
        if json_content_type:
            headers["Content-Type"] = "application/json"
        should_retry = method.upper() in _AUTO_RETRY_METHODS if retry is None else retry
        response = self._send_with_retry(
            method=method,
            url=f"{self.base_url}{self.api_path}{endpoint}",
            headers=headers,
            data=data,
            params=params,
            retry=should_retry,
        )
        response.raise_for_status()
        return response

    def _send_with_retry(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        data: Any,
        params: dict[str, Any] | None,
        retry: bool,
    ) -> requests.Response:
        """Issue the transport call, optionally retrying CONNECTION failures with backoff.

        When ``retry`` is true, retries ``requests.ConnectionError`` (connect blips, DNS,
        connect-timeout, or a mid-flight reset) with backoff before a final attempt whose
        error propagates. Two things are NEVER retried, regardless of ``retry``:

        - **Read timeouts:** a slow server means retrying would only multiply the hang
          (cf. the files-inbox 3A publish block); ``ReadTimeout`` is not a
          ``ConnectionError``, so it falls straight through.
        - **HTTP status errors:** ``raise_for_status`` runs in ``_request``, outside this
          loop, so control-flow codes like 409 are never re-sent.

        ``retry`` is gated by the caller because a ``ConnectionError`` can be a reset that
        arrives AFTER the server committed the request, so re-sending a non-idempotent
        write (POST create, etc.) would double-apply it — hence only reads auto-retry and
        writers must opt in explicitly.
        """
        if retry:
            for wait in _TRANSPORT_RETRY_WAITS:
                try:
                    return requests.request(
                        method=method, url=url, headers=headers, json=data, params=params, timeout=30
                    )
                except requests.ConnectionError:
                    time.sleep(wait)
        return requests.request(method=method, url=url, headers=headers, json=data, params=params, timeout=30)

    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Any = None,
        params: dict[str, Any] | None = None,
        retry: bool | None = None,
    ) -> dict[str, Any]:
        """Issue a JSON request and return the parsed dict body (``{}`` when empty).

        ``retry`` follows :meth:`_request`: ``None`` auto-retries reads only; ``True``
        opts a safe-to-repeat write into connection-retry.
        """
        response = self._request(method, endpoint, data=data, params=params, retry=retry)
        result: dict[str, Any] = response.json() if response.content else {}
        return result
