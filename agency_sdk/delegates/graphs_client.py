"""Knowledge-graph reads through comand: the org's knowledge-graph catalogue."""

from __future__ import annotations

from agency_sdk.delegates.base_client import BaseDelegateClient
from agency_sdk.delegates.graphs_dto import KnowledgeGraphList

__all__ = ["AgencyGraphsClient"]


class AgencyGraphsClient(BaseDelegateClient):
    api_path = "/api/knowledge-graphs"

    def list(self, organisation_id: int, *, page: int | None = None, size: int | None = None) -> KnowledgeGraphList:
        """Return one page of the org's knowledge-graph catalogue (``GET /api/knowledge-graphs?o=&p=&s=``).

        ``page`` is 0-based. ``p`` / ``s`` are sent only when given, so an omitted value takes the server default
        (``page=0``, ``size=10``); the SDK applies no defaults, limits or range checks of its own. Items are sorted
        by ``code``. The result's ``page`` carries ``page``, ``size`` and ``total`` (entries across all pages). A
        page past the end, or ``size=0``, is an empty page; a negative value is answered with a 500, raised as
        ``requests.HTTPError``.
        """
        params = {"o": str(organisation_id)}
        if page is not None:
            params["p"] = str(page)
        if size is not None:
            params["s"] = str(size)
        body = self._make_request("GET", "", params=params)
        catalogue: KnowledgeGraphList = KnowledgeGraphList.model_validate(body)
        return catalogue
