"""Knowledge-graph reads through comand: the org's knowledge-graph catalogue."""

from __future__ import annotations

from agency_sdk.delegates.base_client import BaseDelegateClient
from agency_sdk.delegates.graphs_dto import KnowledgeGraphList

__all__ = ["AgencyGraphsClient"]


class AgencyGraphsClient(BaseDelegateClient):
    api_path = "/api/knowledge-graphs"

    def list(self, organisation_id: int) -> KnowledgeGraphList:
        """Return the org's knowledge-graph catalogue (``GET /api/knowledge-graphs?o=``). Not paged."""
        body = self._make_request("GET", "", params={"o": str(organisation_id)})
        catalogue: KnowledgeGraphList = KnowledgeGraphList.model_validate(body)
        return catalogue
