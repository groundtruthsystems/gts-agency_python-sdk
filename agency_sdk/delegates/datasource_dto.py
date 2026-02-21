from typing import Optional, List

from pydantic import BaseModel, ConfigDict

from agency_sdk.delegates.datasets_dto import Page


def _to_camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


class DatasourceSummary(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: str
    organization_id: int
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    datasource_type: str
    status: str
    table_count: Optional[int] = None
    last_sync_at: Optional[str] = None
    last_sync_status: Optional[str] = None
    created_at: str
    updated_at: str


class DatasourceDetail(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: str
    organization_id: int
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    datasource_type: str
    status: str
    table_count: Optional[int] = None
    last_sync_at: Optional[str] = None
    last_sync_status: Optional[str] = None
    last_sync_error: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    database_name: Optional[str] = None
    username: Optional[str] = None
    connection_options: Optional[str] = None
    last_connection_test: Optional[str] = None
    last_connection_error: Optional[str] = None
    created_at: str
    updated_at: str
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


class DatasourcesPagedResult(BaseModel):
    page: Page
    items: List[DatasourceSummary]


class DatasourceTable(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    table_name: str
    table_schema: Optional[str] = None
    table_type: Optional[str] = None
    table_comment: Optional[str] = None
    row_count: Optional[int] = None
    synced_at: Optional[str] = None


class DatasourceTableColumn(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    column_name: str
    ordinal_position: int
    data_type: str
    is_nullable: bool
    column_default: Optional[str] = None
    column_comment: Optional[str] = None


class DatasourceTableConstraint(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    constraint_name: str
    constraint_type: str
    column_names: List[str]
    referenced_table: Optional[str] = None
    referenced_column: Optional[str] = None


class DatasourceTableDetail(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    table_name: str
    table_schema: Optional[str] = None
    table_type: Optional[str] = None
    table_comment: Optional[str] = None
    row_count: Optional[int] = None
    synced_at: Optional[str] = None
    columns: List[DatasourceTableColumn]
    constraints: List[DatasourceTableConstraint]


class DatasourceTablesPagedResult(BaseModel):
    page: Page
    items: List[DatasourceTable]
