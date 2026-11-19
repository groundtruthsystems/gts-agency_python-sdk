"""Offline stand-in for comand's paged ``GET /api/knowledge-graphs`` (test helper, not a test module).

Mirrors ``list_catalog_page`` / ``page_entries`` in gts-agency ``knowledge_graph_service.rs`` (cf1d53a3):
sort by ``code``, defaults ``p=0`` / ``s=10``, offset ``p*s``, an empty page past the end or at ``s=0`` with
the real ``total``, and a 500 ``SERVICE_ERROR`` envelope for negative ``p``/``s`` or an overflowing ``p*s``.
Synthetic data only.
"""

from __future__ import annotations

from typing import Any

import requests

from agency_sdk.test.conftest import StubResponse

I32_MAX = 2**31 - 1
INVALID_PAGE_BODY = {"error": {"type": "SERVICE_ERROR", "req_uuid": "req-test", "message": "Invalid page request"}}


def entry(code: str, *, enabled: bool = True) -> dict[str, Any]:
    return {"code": code, "name": f"Sample graph {code}", "description": "", "enabled": enabled}


def page_body(entries: list[dict[str, Any]], page: int, size: int) -> tuple[int, dict[str, Any]]:
    """Return ``(status, body)`` the server would send for one page request."""
    if page < 0 or size < 0 or page * size > I32_MAX:
        return 500, INVALID_PAGE_BODY
    ordered = sorted(entries, key=lambda e: e["code"])
    start = min(page * size, len(ordered))
    return 200, {"page": {"page": page, "size": size, "total": len(ordered)}, "items": ordered[start : start + size]}


class PagedServer:
    """A ``requests.request`` replacement that serves ``entries`` with the server's paging rules."""

    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self.entries = entries
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method: str, url: str, **kwargs: Any) -> StubResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        params = kwargs.get("params") or {}
        status, body = page_body(self.entries, int(params.get("p", 0)), int(params.get("s", 10)))
        return StubResponse(json_data=body, status_code=status)


def serve(monkeypatch: Any, entries: list[dict[str, Any]]) -> PagedServer:
    server = PagedServer(entries)
    monkeypatch.setattr(requests, "request", server)
    return server


def walk(graphs: Any, organisation_id: int, size: int = 10) -> list[str]:
    """The documented walk (docs/graphs.md): stop on the last page by ``total``, or on an empty page."""
    page, codes = 0, []
    while True:
        result = graphs.list(organisation_id, page=page, size=size)
        codes.extend(e.code for e in result.items)
        if not result.items or (page + 1) * size >= result.page.total:
            return codes
        page += 1
