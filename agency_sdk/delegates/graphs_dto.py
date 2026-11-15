"""DTOs and exceptions for the knowledge-graphs delegate (snake_case, matching the API).

- :class:`KnowledgeGraphList` / :class:`KnowledgeGraphEntry` mirror comand's
  ``ListKnowledgeGraphsResponse`` (``service/knowledge_graphs/knowledge_graph_service.rs``):
  the built-in catalogue with the org's ``enabled`` flag. ``credential_variable_id`` and
  ``config`` are omitted by the server when unset, and both are hidden from ``repr`` because
  ``config`` is operator configuration that should never reach a log line.
- :class:`ResolvedEntity` is the minimum the SDK checks on the ontology service's
  ``/api/models/_/{code}?source=code`` response before using it: a UUID-shaped ``bid``
  (it becomes a URL path segment) and a ``class``.
- :class:`ConditionSubgraph` keeps the ontology documents as raw dicts: they are open and
  unmodelled, like ``annotations().get_graph``.
"""

from __future__ import annotations

import re
from typing import Any

import requests
from pydantic import BaseModel, ConfigDict, Field, field_validator

#: The ontology service's ``bid`` is a UUID; anything else is refused before it reaches a URL path.
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

#: The entity class a condition subgraph must be rooted at.
CONDITION_CLASS = "condition"


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


class ResolvedEntity(BaseModel):
    """The fields of a resolved ontology entity the SDK relies on; the full document is kept separately."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    bid: str
    entity_class: str = Field(alias="class")

    @field_validator("bid")
    @classmethod
    def _bid_is_uuid(cls, value: str) -> str:
        if not _UUID_RE.fullmatch(value):
            raise ValueError("bid must be a UUID")
        return value


class ConditionSubgraph(BaseModel):
    """A condition's known subgraph: the resolved root entity plus the walk's vertices and edges."""

    model_config = ConfigDict(extra="allow")

    root: dict[str, Any]
    vertices: list[dict[str, Any]]
    edges: list[dict[str, Any]]


class ConditionNotFound(requests.HTTPError):
    """The knowledge graph has no condition for this code (the ontology service answered 404 not found).

    This is the only signal for the agent's *create* mode. Any other failure is a plain ``HTTPError``.
    The message never repeats the code.
    """

    def __init__(self, *, response: requests.Response | None = None) -> None:
        super().__init__("Condition not found in the knowledge graph", response=response)


class ConditionClassMismatch(LookupError):
    """The code resolved to an entity that is not a condition; ``entity_class`` names what it is.

    Not *not found*: the agent must stop, not create.
    """

    def __init__(self, entity_class: str) -> None:
        super().__init__(f"Code resolved to a '{entity_class}' entity, not a condition")
        self.entity_class = entity_class
