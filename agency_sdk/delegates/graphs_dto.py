"""DTOs for the knowledge-graphs delegate (snake_case, matching the API).

:class:`KnowledgeGraphList` / :class:`KnowledgeGraphEntry` mirror comand's ``ListKnowledgeGraphsResponse``
(``service/knowledge_graphs/knowledge_graph_service.rs``): the built-in catalogue with the org's ``enabled``
flag. ``credential_variable_id`` and ``config`` are omitted by the server when unset, and both are hidden
from ``repr`` because ``config`` is operator configuration that should never reach a log line.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
    """``GET /api/knowledge-graphs?o=`` response. Not paged."""

    model_config = ConfigDict(extra="allow")

    items: list[KnowledgeGraphEntry]
