import requests

from typing import Optional, Dict

from agency_sdk.credentials import CredentialsSupplier
from agency_sdk.delegates.ontology_dto import MappingsPagedResult, EntityDatasourceMappingDetail


class AgencyOntologyClient:

    def __init__(self, token_supplier: CredentialsSupplier, base_url: str = 'http://localhost:9003'):
        self.base_url = base_url.rstrip('/')
        self.token_supplier = token_supplier

    def _make_request(
            self,
            method: str,
            endpoint: str,
            params: Optional[Dict] = None
    ) -> requests.Response:
        """Make an HTTP request to the API and return the raw response.

        Args:
            method: HTTP method ('GET', 'POST', etc.)
            endpoint: API endpoint path
            params: Query parameters

        Returns:
            Raw requests.Response object
        """
        url = f"{self.base_url}/api/ontologies{endpoint}"
        response = requests.request(
            method=method,
            url=url,
            headers={
                'Authorization': f'Bearer {self.token_supplier.bearer_token()}',
            },
            params=params
        )
        response.raise_for_status()
        return response

    def export(
            self,
            ontology_id: str,
            organisation_id: int,
            format: Optional[str] = None,
            branch: Optional[str] = None,
            version: Optional[str] = None
    ) -> str:
        """Export an ontology in the specified format.

        Args:
            ontology_id: The ontology ID
            organisation_id: The organisation ID
            format: Export format (json, owl, turtle, toon, ison)
            branch: Branch name (defaults to "main" server-side)
            version: Version identifier

        Returns:
            Raw response text content
        """
        params = {"o": str(organisation_id)}
        if format is not None:
            params["format"] = format
        if branch is not None:
            params["branch"] = branch
        if version is not None:
            params["version"] = version
        response = self._make_request('GET', f"/{ontology_id}/export", params=params)
        return response.text

    def _make_json_request(
            self,
            method: str,
            endpoint: str,
            params: Optional[Dict] = None
    ) -> Dict:
        """Make an HTTP request to the API and return parsed JSON.

        Args:
            method: HTTP method ('GET', 'POST', etc.)
            endpoint: API endpoint path
            params: Query parameters

        Returns:
            Response data as dictionary
        """
        url = f"{self.base_url}/api/ontologies{endpoint}"
        response = requests.request(
            method=method,
            url=url,
            headers={
                'Authorization': f'Bearer {self.token_supplier.bearer_token()}',
                'Content-Type': 'application/json'
            },
            params=params
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    def list_mappings(
            self,
            ontology_id: str,
            organisation_id: int,
            entity_id: Optional[str] = None,
            page: int = 0,
            size: int = 10
    ) -> MappingsPagedResult:
        """List entity-datasource mappings for an ontology.

        Args:
            ontology_id: The ontology ID
            organisation_id: The organisation ID
            entity_id: Optional entity ID to filter by
            page: Page number (0-indexed)
            size: Page size

        Returns:
            Paged list of mappings
        """
        params = {"o": str(organisation_id), "s": str(size), "p": str(page)}
        if entity_id is not None:
            params["entity_id"] = entity_id
        return MappingsPagedResult(**self._make_json_request('GET', f"/{ontology_id}/mappings", params=params))

    def get_mapping(
            self,
            ontology_id: str,
            mapping_id: str,
            organisation_id: int
    ) -> EntityDatasourceMappingDetail:
        """Get a specific entity-datasource mapping.

        Args:
            ontology_id: The ontology ID
            mapping_id: The mapping ID
            organisation_id: The organisation ID

        Returns:
            Detailed mapping information
        """
        params = {"o": str(organisation_id)}
        return EntityDatasourceMappingDetail(**self._make_json_request('GET', f"/{ontology_id}/mappings/{mapping_id}", params=params))
