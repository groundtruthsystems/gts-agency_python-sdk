from pathlib import Path

import requests

from agency_sdk.delegates.base_client import BaseDelegateClient
from agency_sdk.delegates.datasets_dto import (
    DatasetFilesResponse,
    DatasetResponse,
    DatasetsPagedResult,
    SignedUrlResponse,
)


class AgencyDatasetsClient(BaseDelegateClient):
    api_path = "/api/datasets"

    def list(self, organisation_id: int, page: int = 0, size: int = 10) -> DatasetsPagedResult:
        """List all datasets for an organisation."""
        params = {"o": str(organisation_id), "s": str(size), "p": str(page)}
        return DatasetsPagedResult(**self._make_request("GET", "", params=params))

    def get(self, dataset_id: str, organisation_id: int, version: str = "latest") -> DatasetResponse:
        """Get a specific dataset by ID."""
        params = {"o": str(organisation_id), "v": version}
        return DatasetResponse(**self._make_request("GET", f"/{dataset_id}", params=params))

    def filesystem_list(
        self,
        dataset_id: str,
        organisation_id: int,
        version: str,
        path: str | None = None,
    ) -> DatasetFilesResponse:
        """List files and directories in a dataset filesystem."""
        params: dict[str, str] = {"o": str(organisation_id), "v": version}
        if path is not None:
            params["path"] = path
        return DatasetFilesResponse(**self._make_request("GET", f"/{dataset_id}/filesystem", params=params))

    def filesystem_signed_url(
        self,
        dataset_id: str,
        organisation_id: int,
        version: str,
        file_path: str,
    ) -> SignedUrlResponse:
        """Get a signed URL for downloading a dataset file."""
        params = {"o": str(organisation_id), "v": version}
        data = {
            "file_path": f"/{file_path}",
            "version": f"{version}",
        }
        return SignedUrlResponse(
            **self._make_request("POST", f"/{dataset_id}/filesystem/_signed-url", data=data, params=params)
        )

    def clone_dataset(
        self,
        dataset_id: str,
        organisation_id: int,
        target_path: str,
        version: str = "latest",
    ) -> None:
        """Clone a dataset to a given directory.

        The process is:
        1. Get a dataset.
        2. Using the version from the get response call filesystem_list.
        3. Using that response get signed urls for all files and recursively
           call filesystem_list for directories.
        4. With all the signed urls download the files to the target directory
           and preserve the directory structure.
        """
        dataset = self.get(dataset_id, organisation_id, version)
        actual_version = str(dataset.version)

        target_dir = Path(target_path)
        target_dir.mkdir(parents=True, exist_ok=True)

        self._clone_directory(dataset_id, organisation_id, actual_version, "", target_dir)

    def _clone_directory(
        self,
        dataset_id: str,
        organisation_id: int,
        version: str,
        path: str,
        target_dir: Path,
    ) -> None:
        """Recursively clone a directory and its contents."""
        files_response = self.filesystem_list(dataset_id, organisation_id, version, path if path else None)

        for item in files_response.items:
            item_target_path = target_dir / item.name

            if item.item_type == "directory":
                item_target_path.mkdir(exist_ok=True)
                sub_path = f"{path}/{item.name}" if path else item.name
                self._clone_directory(dataset_id, organisation_id, version, sub_path, item_target_path)

            elif item.item_type == "file":
                signed_url_response = self.filesystem_signed_url(dataset_id, organisation_id, version, item.path)
                self._download_file(signed_url_response.signed_url, item_target_path)

    def _download_file(self, signed_url: str, target_path: Path) -> None:
        """Download a file from a signed URL to the target path."""
        response = requests.get(signed_url, timeout=60)
        response.raise_for_status()
        target_path.write_bytes(response.content)
