from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agency_sdk.delegates.datasets_dto import Page

#: Query-param aliases the Control Plane treats as `application/zip` (binary).
#: `export()` refuses these; use :meth:`AgencyOntologyClient.export_bytes`.
BINARY_EXPORT_FORMATS = frozenset({"package-zip", "package-separate", "zip"})


def is_binary_export_format(export_format: str | None) -> bool:
    """True when ``export_format`` is a zip-package alias (case-insensitive)."""
    return export_format is not None and export_format.lower() in BINARY_EXPORT_FORMATS


class OntologyStatus(StrEnum):
    """Lifecycle status of an ontology (lowercase on the wire)."""

    DRAFT = "draft"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class Ontology(BaseModel):
    """List-view ontology from ``GET /api/ontologies``.

    Wire format is snake_case (serde defaults). ``kind`` defaults to ``domain``
    when omitted, matching the Control Plane. ``organization_id`` is the server
    spelling.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    display_name: str | None = None
    description: str | None = None
    status: OntologyStatus
    kind: str = "domain"
    created_at: str
    updated_at: str
    organization_id: int


class OntologiesPagedResult(BaseModel):
    """Page-wrapped ``GET /api/ontologies`` response."""

    page: Page
    items: list[Ontology]


def _to_camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


class EntityDatasourceMapping(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: str
    ontology_id: str
    datasource_id: str
    datasource_name: str | None = None
    entity_id: str
    entity_label: str | None = None
    mapping_type: str
    description: str | None = None
    status: str
    target_name: str | None = None
    created_at: str
    updated_at: str


class RdbmsColumnMapping(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: str | None = None
    property_name: str
    column_id: str | None = None
    column_name: str
    alias: str | None = None
    transformation: str | None = None
    ordinal_position: int | None = None


class RdbmsMappingExtension(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    table_id: str | None = None
    table_name: str
    table_schema: str | None = None


class EntityDatasourceMappingDetail(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: str
    ontology_id: str
    datasource_id: str
    datasource_name: str | None = None
    entity_id: str
    entity_label: str | None = None
    mapping_type: str
    description: str | None = None
    status: str
    target_name: str | None = None
    generated_query: str | None = None
    rdbms: RdbmsMappingExtension | None = None
    column_mappings: list[RdbmsColumnMapping] | None = None
    created_at: str
    updated_at: str
    created_by: str | None = None
    updated_by: str | None = None


class MappingsPagedResult(BaseModel):
    page: Page
    items: list[EntityDatasourceMapping]


class QueryFilter(BaseModel):
    """A filter condition for an entity data query."""

    property: str
    operator: str
    value: Any | None = None


class QueryRequest(BaseModel):
    """Request body for the entity data _query endpoint."""

    filters: list[QueryFilter] = []
    page: int = 0
    size: int = 25


class QueryMappingInfo(BaseModel):
    """Resolved mapping metadata returned with query results."""

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: str
    datasource_id: str
    datasource_name: str
    entity_label: str | None = None
    status: str
    generated_query: str | None = None


class QueryResult(BaseModel):
    """Response from the entity data _query endpoint."""

    items: list[dict[str, Any]]
    page: Page
    mapping: QueryMappingInfo


class PropertySnapshot(BaseModel):
    """A reusable ``owl:ObjectProperty`` declaration from a JSON export.

    Wire format is snake_case (serde defaults on the Control Plane snapshot).
    ``extra="allow"`` so additive server fields do not break agents.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    uri: str
    label: str
    description: str | None = None
    sub_property_of: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class EntitySnapshot(BaseModel):
    """One entity in a JSON ontology export (class, individual, or property)."""

    model_config = ConfigDict(extra="allow")

    id: str
    uri: str
    label: str
    description: str | None = None
    entity_type: str
    properties: dict[str, Any] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)


class RelationSnapshot(BaseModel):
    """One relation in a JSON ontology export (``SubClassOf`` or ``ObjectProperty``)."""

    model_config = ConfigDict(extra="allow")

    id: str
    uri: str
    label: str
    description: str | None = None
    source_id: str
    target_id: str
    relation_type: str
    property_id: str | None = None
    min_count: int | None = None
    max_count: int | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class OntologySnapshot(BaseModel):
    """JSON body of ``GET /api/ontologies/{id}/export?format=json``.

    The agent-facing shape: iterate ``entities`` / ``relations`` / ``properties``
    to decide how to treat data. Maps default to empty when a key is omitted.
    """

    model_config = ConfigDict(extra="allow")

    entities: dict[str, EntitySnapshot] = Field(default_factory=dict)
    relations: dict[str, RelationSnapshot] = Field(default_factory=dict)
    properties: dict[str, PropertySnapshot] = Field(default_factory=dict)
