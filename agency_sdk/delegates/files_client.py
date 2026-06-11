"""Client for the tenant file storage API (/api/files)."""

from typing import Any

import requests

from agency_sdk.credentials import CredentialsSupplier
from agency_sdk.delegates.files_dto import FilesPagedResult


class AgencyFilesClient:
    def __init__(self, token_supplier: CredentialsSupplier, base_url: str = "http://localhost:9003"):
        self.base_url = base_url.rstrip("/")
        self.token_supplier = token_supplier

    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an HTTP request to the API."""
        url = f"{self.base_url}/api/files{endpoint}"
        response = requests.request(
            method=method,
            url=url,
            headers={
                "Authorization": f"Bearer {self.token_supplier.bearer_token()}",
                "Content-Type": "application/json",
            },
            json=data,
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json() if response.content else {}
        return result

    def list(self, organisation_id: int, path: str = "", page: int = 0, size: int = 50) -> FilesPagedResult:
        """List files and folders at a logical path (folders first, paginated).

        Args:
            organisation_id: The organisation ID.
            path: Directory path to list (default: root, "").
            page: Zero-indexed page number.
            size: Page size (server default 50).
        """
        params = {"o": str(organisation_id), "path": path, "p": str(page), "s": str(size)}
        return FilesPagedResult(**self._make_request("GET", "", params=params))
