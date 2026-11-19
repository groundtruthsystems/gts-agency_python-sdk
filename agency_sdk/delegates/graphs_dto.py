"""DTOs for the knowledge-graphs delegate (snake_case, matching the API).

:class:`KnowledgeGraphList` mirrors comand's ``PagedResult<KnowledgeGraphEntry>``
(``service/knowledge_graphs/knowledge_graph_service.rs``): one page of the built-in catalogue, sorted by ``code``,
with the org's ``enabled`` flag, plus the standard ``page`` metadata (:class:`agency_sdk.domain.Page`). It replaced
the unpaged ``ListKnowledgeGraphsResponse``. ``credential_variable_id`` and ``config`` are omitted by the server
when unset, and both are hidden from ``repr`` because ``config`` is operator configuration that should never reach a
log line.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agency_sdk.domain import Page


class KnowledgeGraphEntry(BaseModel):
    """One knowledge graph in the org's catalogue."""

    model_config = ConfigDict(extra="allow")

    code: str
    name: str
    description: str
    enabled: bool
    credential_variable_id: str | None = Field(default=None, repr=False)
    config: Any = Field(default=None, repr=False)


class KnowledgeGraphList(BaseModel):
    """``GET /api/knowledge-graphs?o=&p=&s=`` response: one 0-based page of the catalogue, sorted by ``code``.

    ``page.page`` and ``page.size`` echo the request (or the server defaults ``0`` / ``10``); ``page.total`` counts
    every entry across all pages.
    """

    model_config = ConfigDict(extra="allow")

    page: Page
    items: list[KnowledgeGraphEntry]
