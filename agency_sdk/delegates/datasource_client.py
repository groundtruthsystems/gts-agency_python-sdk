from agency_sdk.delegates.base_client import BaseDelegateClient
from agency_sdk.delegates.datasource_dto import (
    DatasourceDetail,
    DatasourceTableDetail,
    DatasourceTablesPagedResult,
    DatasourcesPagedResult,
)


class AgencyDatasourceClient(BaseDelegateClient):
    api_path = "/api/datasources"

    def list(self, organisation_id: int, page: int = 0, size: int = 10) -> DatasourcesPagedResult:
        """List all datasources for an organisation."""
        params = {"o": str(organisation_id), "s": str(size), "p": str(page)}
        return DatasourcesPagedResult(**self._make_request("GET", "", params=params))

    def get(self, datasource_id: str, organisation_id: int) -> DatasourceDetail:
        """Get a specific datasource by ID."""
        params = {"o": str(organisation_id)}
        return DatasourceDetail(**self._make_request("GET", f"/{datasource_id}", params=params))

    def list_tables(
        self,
        datasource_id: str,
        organisation_id: int,
        page: int = 0,
        size: int = 10,
    ) -> DatasourceTablesPagedResult:
        """List tables for a datasource."""
        params = {"o": str(organisation_id), "s": str(size), "p": str(page)}
        return DatasourceTablesPagedResult(**self._make_request("GET", f"/{datasource_id}/tables", params=params))

    def get_table(
        self,
        datasource_id: str,
        table_name: str,
        organisation_id: int,
    ) -> DatasourceTableDetail:
        """Get detailed information about a specific table."""
        params = {"o": str(organisation_id)}
        return DatasourceTableDetail(
            **self._make_request("GET", f"/{datasource_id}/tables/{table_name}", params=params)
        )
