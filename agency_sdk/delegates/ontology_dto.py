from typing import Optional, List

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
    datasource_name: Optional[str] = None
    entity_id: str
    entity_label: Optional[str] = None
    mapping_type: str
    description: Optional[str] = None
    status: str
    target_name: Optional[str] = None
    created_at: str
    updated_at: str


class RdbmsColumnMapping(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: Optional[str] = None
    property_name: str
    column_id: Optional[str] = None
    column_name: str
    alias: Optional[str] = None
    transformation: Optional[str] = None
    ordinal_position: Optional[int] = None


class RdbmsMappingExtension(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    table_id: Optional[str] = None
    table_name: str
    table_schema: Optional[str] = None


class EntityDatasourceMappingDetail(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: str
    ontology_id: str
    datasource_id: str
    datasource_name: Optional[str] = None
    entity_id: str
    entity_label: Optional[str] = None
    mapping_type: str
    description: Optional[str] = None
    status: str
    target_name: Optional[str] = None
    generated_query: Optional[str] = None
    rdbms: Optional[RdbmsMappingExtension] = None
    column_mappings: Optional[List[RdbmsColumnMapping]] = None
    created_at: str
    updated_at: str
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


class MappingsPagedResult(BaseModel):
    page: Page
    items: List[EntityDatasourceMapping]
