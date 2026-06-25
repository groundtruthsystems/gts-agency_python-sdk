from agency_sdk.delegates.base_client import BaseDelegateClient
from agency_sdk.delegates.ontology_dto import (
    EntityDatasourceMappingDetail,
    MappingsPagedResult,
    QueryFilter,
    QueryRequest,
    QueryResult,
)


class AgencyOntologyClient(BaseDelegateClient):
    api_path = "/api/ontologies"

    def export(
        self,
        ontology_id: str,
        organisation_id: int,
        export_format: str | None = None,
        branch: str | None = None,
        version: str | None = None,
    ) -> str:
        """Export an ontology in the specified format.

        Args:
            ontology_id: The ontology ID
            organisation_id: The organisation ID
            export_format: Export format (json, owl, turtle, toon, ison)
            branch: Branch name (defaults to "main" server-side)
            version: Version identifier

        Returns:
            Raw response text content
        """
        params: dict[str, str] = {"o": str(organisation_id)}
        if export_format is not None:
            params["format"] = export_format
        if branch is not None:
            params["branch"] = branch
        if version is not None:
            params["version"] = version
        response = self._request("GET", f"/{ontology_id}/export", params=params, json_content_type=False)
        text: str = response.text
        return text

    def list_mappings(
        self,
        ontology_id: str,
        organisation_id: int,
        entity_id: str | None = None,
        page: int = 0,
        size: int = 10,
    ) -> MappingsPagedResult:
        """List entity-datasource mappings for an ontology."""
        params: dict[str, str] = {"o": str(organisation_id), "s": str(size), "p": str(page)}
        if entity_id is not None:
            params["entity_id"] = entity_id
        return MappingsPagedResult(**self._make_request("GET", f"/{ontology_id}/mappings", params=params))

    def get_mapping(
        self,
        ontology_id: str,
        mapping_id: str,
        organisation_id: int,
    ) -> EntityDatasourceMappingDetail:
        """Get a specific entity-datasource mapping."""
        params = {"o": str(organisation_id)}
        return EntityDatasourceMappingDetail(
            **self._make_request("GET", f"/{ontology_id}/mappings/{mapping_id}", params=params)
        )

    def query_entity(
        self,
        ontology_id: str,
        entity_id: str,
        organisation_id: int,
        filters: list[QueryFilter] | None = None,
        page: int = 0,
        size: int = 25,
    ) -> QueryResult:
        """Query entity data through its active entity-datasource mapping.

        Args:
            ontology_id: The ontology identifier.
            entity_id: The entity identifier within the ontology.
            organisation_id: The organisation identifier.
            filters: Filter conditions combined with AND. Defaults to no filters.
            page: Zero-indexed page number. Defaults to 0.
            size: Results per page (max 100). Defaults to 25.

        Returns:
            Query results with items, pagination info, and resolved mapping metadata.
        """
        params = {"o": str(organisation_id)}
        body = QueryRequest(filters=filters or [], page=page, size=size)
        return QueryResult(
            **self._make_request(
                "POST",
                f"/{ontology_id}/entities/{entity_id}/data/_query",
                data=body.model_dump(mode="json"),
                params=params,
            )
        )
