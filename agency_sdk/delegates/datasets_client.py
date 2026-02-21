import requests
import os
import urllib.request
from pathlib import Path

from typing import Optional, Dict, List

from agency_sdk.credentials import CredentialsSupplier
from agency_sdk.delegates.datasets_dto import DatasetsPagedResult, DatasetResponse, DatasetFilesResponse, SignedUrlResponse


class AgencyDatasetsClient:

    def __init__(self, token_supplier: CredentialsSupplier, base_url: str = 'http://localhost:9003'):
        self.base_url = base_url.rstrip('/')
        self.token_supplier = token_supplier

    def _make_request(
            self,
            method: str,
            endpoint: str,
            data: Optional[Dict] = None,
            params: Optional[Dict] = None
    ) -> Dict:
        """Make an HTTP request to the API.

        Args:
            method: HTTP method ('GET', 'POST', etc.)
            endpoint: API endpoint path
            data: Request body data
            params: Query parameters

        Returns:
            Response data as dictionary
        """
        url = f"{self.base_url}/api/datasets{endpoint}"
        response = requests.request(
            method=method,
            url=url,
            headers={
                'Authorization': f'Bearer {self.token_supplier.bearer_token()}',
                'Content-Type': 'application/json'
            },
            json=data,
            params=params
        )
        response.raise_for_status()
        return response.json() if response.content else {}


    def list(self, organisation_id: int, page: int = 0, size: int = 10) -> DatasetsPagedResult:
        """List all prompts for an organisation."""
        params = {"o": str(organisation_id), "s": str(size), "p": str(page)}
        return DatasetsPagedResult(**self._make_request('GET', '', params=params))


    def get(self, dataset_id: str, organisation_id: int, version: str = "latest") -> DatasetResponse:
        """Get a specific prompt by ID."""
        params = {"o": str(organisation_id), "v": version}
        return DatasetResponse(**self._make_request('GET', f"/{dataset_id}", params=params))



    def filesystem_list(self, dataset_id: str, organisation_id: int, version: str, path: str = None) -> DatasetFilesResponse:
        """Get a specific prompt by ID."""
        params = {"o": str(organisation_id), "v": version}
        if path is not None:
            params["path"] = path
        return DatasetFilesResponse(**self._make_request('GET', f"/{dataset_id}/filesystem", params=params))


    def filesystem_signed_url(self, dataset_id: str, organisation_id: int, version: str, file_path: str) -> SignedUrlResponse:
        """Get a specific prompt by ID."""
        params = {"o": str(organisation_id), "v": version}
        data = {
            "file_path": f"/{file_path}",
            "version": f"{version}",
        }
        return SignedUrlResponse(**self._make_request('POST', f"/{dataset_id}/filesystem/_signed-url", data=data, params=params))

    def clone_dataset(self, dataset_id: str, organisation_id: int, target_path: str, version: str = "latest"):
        """
        Clone a dataset to a given directory.

        The process is:
        1. Get a dataset.
        2. Using the version from the get response call filesystem_list.
        3. Using that response get signed urls for all files and recursively call filesystem_list for directories.
        4. With all the signed urls download the files to the target directory and preserve the directory structure.
        """
        # Step 1: Get dataset info
        dataset = self.get(dataset_id, organisation_id, version)
        actual_version = str(dataset.version)
        
        # Create target directory
        target_dir = Path(target_path)
        target_dir.mkdir(parents=True, exist_ok=True)
        
        # Step 2: Start filesystem traversal from root
        self._clone_directory(dataset_id, organisation_id, actual_version, "", target_dir)
    
    def _clone_directory(self, dataset_id: str, organisation_id: int, version: str, path: str, target_dir: Path):
        """Recursively clone a directory and its contents."""
        # List contents of current directory
        files_response = self.filesystem_list(dataset_id, organisation_id, version, path if path else None)
        
        for item in files_response.items:
            item_target_path = target_dir / item.name
            
            if item.item_type == "directory":
                # Create directory and recursively clone its contents
                item_target_path.mkdir(exist_ok=True)
                sub_path = f"{path}/{item.name}" if path else item.name
                self._clone_directory(dataset_id, organisation_id, version, sub_path, item_target_path)
            
            elif item.item_type == "file":
                # Get signed URL and download file
                signed_url_response = self.filesystem_signed_url(dataset_id, organisation_id, version, item.path)
                self._download_file(signed_url_response.signed_url, item_target_path)
    
    def _download_file(self, signed_url: str, target_path: Path):
        """Download a file from a signed URL to the target path."""
        urllib.request.urlretrieve(signed_url, target_path)