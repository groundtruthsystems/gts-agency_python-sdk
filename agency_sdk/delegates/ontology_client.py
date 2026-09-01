"""Client for the ontologies API (``/api/ontologies``).

:meth:`AgencyOntologyClient.list` is ``GET /api/ontologies`` (paged, optional
``kind`` filter) so a caller can resolve a name → id. Export is
``GET /api/ontologies/{id}/export``. Text formats (JSON, Turtle/OWL, SHACL,
package) return ``str``. ``package-zip`` is binary: :meth:`export` refuses it
and :meth:`export_bytes` returns ``bytes``. Agents that need classes, relations,
and properties to drive how they treat data should call :meth:`export_snapshot`.
"""

from __future__ import annotations

import builtins  # the `list()` method shadows the builtin, so annotations use builtins.list[...]
from typing import Any

from agency_sdk.delegates.base_client import BaseDelegateClient
from agency_sdk.delegates.ontology_dto import (
    EntityDatasourceMappingDetail,
    MappingsPagedResult,
    OntologiesPagedResult,
    OntologySnapshot,
    QueryFilter,
    QueryRequest,
    QueryResult,
    is_binary_export_format,
)


class AgencyOntologyClient(BaseDelegateClient):
    api_path = "/api/ontologies"

    def list(
        self,
        organisation_id: int,
        *,
        kind: str | None = None,
        page: int = 0,
        size: int = 10,
    ) -> OntologiesPagedResult:
        """List ontologies in the organisation (paged), for resolving a name → id.

        Args:
            organisation_id: The organisation ID.
            kind: Optional filter: ``domain`` (working) or ``upper`` (importable).
            page: Zero-indexed page number. Defaults to 0.
            size: Page size (server default 10).

        Name→id matching is the caller's job; this returns the raw page.
        """
        params: dict[str, str] = {"o": str(organisation_id), "p": str(page), "s": str(size)}
        if kind is not None:
            params["kind"] = kind
        return OntologiesPagedResult(**self._make_request("GET", "", params=params))

    def _export_params(
        self,
        organisation_id: int,
        export_format: str | None,
        branch: str | None,
        version: str | None,
    ) -> dict[str, str]:
        params: dict[str, str] = {"o": str(organisation_id)}
        if export_format is not None:
            params["format"] = export_format
        if branch is not None:
            params["branch"] = branch
        if version is not None:
            params["version"] = version
        return params

    def _export_response(
        self,
        ontology_id: str,
        organisation_id: int,
        export_format: str | None,
        branch: str | None,
        version: str | None,
    ) -> Any:
        params = self._export_params(organisation_id, export_format, branch, version)
        return self._request("GET", f"/{ontology_id}/export", params=params, json_content_type=False)

    def export(
        self,
        ontology_id: str,
        organisation_id: int,
        export_format: str | None = None,
        branch: str | None = None,
        version: str | None = None,
    ) -> str:
        """Export an ontology as text.

        Args:
            ontology_id: The ontology ID.
            organisation_id: The organisation ID.
            export_format: ``json`` (default), ``owl``/``turtle``, ``shacl``,
                ``shacl-package``, or ``package``. Zip aliases are refused — use
                :meth:`export_bytes`. Unsupported values are a server ``400``.
            branch: Branch whose latest snapshot is exported (server default ``main``).
            version: Published version tag. When set, ``branch`` is not used.

        Returns:
            Response body decoded as text.

        Raises:
            ValueError: If ``export_format`` is a zip-package alias (binary).
        """
        if is_binary_export_format(export_format):
            raise ValueError(f"format {export_format!r} is binary; use export_bytes() for package-zip")
        response = self._export_response(ontology_id, organisation_id, export_format, branch, version)
        text: str = response.text
        return text

    def export_bytes(
        self,
        ontology_id: str,
        organisation_id: int,
        export_format: str | None = None,
        branch: str | None = None,
        version: str | None = None,
    ) -> bytes:
        """Export an ontology as raw bytes (required for ``package-zip``).

        Accepts every format :meth:`export` does, plus ``package-zip`` /
        ``package-separate`` / ``zip``. Write the body to a file as bytes, not text.
        """
        response = self._export_response(ontology_id, organisation_id, export_format, branch, version)
        content: bytes = response.content
        return content

    def export_snapshot(
        self,
        ontology_id: str,
        organisation_id: int,
        branch: str | None = None,
        version: str | None = None,
    ) -> OntologySnapshot:
        """Export the JSON snapshot as a typed model for agent consumption.

        Forces ``format=json``. Use this when an agent needs classes, relations,
        and properties to drive how it treats data.
        """
        response = self._export_response(ontology_id, organisation_id, "json", branch, version)
        body: dict[str, Any] = response.json()
        return OntologySnapshot(**body)

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
        filters: builtins.list[QueryFilter] | None = None,
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
