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
    display_name: str | None = None
    description: str | None = None
    datasource_type: str
    status: str
    table_count: int | None = None
    last_sync_at: str | None = None
    last_sync_status: str | None = None
    created_at: str
    updated_at: str


class DatasourceDetail(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: str
    organization_id: int
    name: str
    display_name: str | None = None
    description: str | None = None
    datasource_type: str
    status: str
    table_count: int | None = None
    last_sync_at: str | None = None
    last_sync_status: str | None = None
    last_sync_error: str | None = None
    host: str | None = None
    port: int | None = None
    database_name: str | None = None
    username: str | None = None
    connection_options: str | None = None
    last_connection_test: str | None = None
    last_connection_error: str | None = None
    created_at: str
    updated_at: str
    created_by: str | None = None
    updated_by: str | None = None


class DatasourcesPagedResult(BaseModel):
    page: Page
    items: list[DatasourceSummary]


class DatasourceTable(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    table_name: str
    table_schema: str | None = None
    table_type: str | None = None
    table_comment: str | None = None
    row_count: int | None = None
    synced_at: str | None = None


class DatasourceTableColumn(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    column_name: str
    ordinal_position: int
    data_type: str
    is_nullable: bool
    column_default: str | None = None
    column_comment: str | None = None


class DatasourceTableConstraint(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    constraint_name: str
    constraint_type: str
    column_names: list[str]
    referenced_table: str | None = None
    referenced_column: str | None = None


class DatasourceTableDetail(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    table_name: str
    table_schema: str | None = None
    table_type: str | None = None
    table_comment: str | None = None
    row_count: int | None = None
    synced_at: str | None = None
    columns: list[DatasourceTableColumn]
    constraints: list[DatasourceTableConstraint]


class DatasourceTablesPagedResult(BaseModel):
    page: Page
    items: list[DatasourceTable]