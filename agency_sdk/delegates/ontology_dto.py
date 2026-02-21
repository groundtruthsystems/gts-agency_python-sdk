from pydantic import BaseModel, ConfigDict

from agency_sdk.delegates.datasets_dto import Page


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