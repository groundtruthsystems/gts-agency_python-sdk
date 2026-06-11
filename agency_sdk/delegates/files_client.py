"""Client for the tenant file storage API (/api/files)."""

import contextlib
import mimetypes
from collections.abc import Sequence
from pathlib import Path
from typing import Any, BinaryIO

import requests

from agency_sdk.credentials import CredentialsSupplier
from agency_sdk.delegates.files_dto import FileEntry, FilesPagedResult, SignedUrlResponse, UploadResult


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

    def signed_url(self, file_id: str, organisation_id: int, expires: int | None = None) -> SignedUrlResponse:
        """Get a temporary signed download URL for a file.

        Args:
            file_id: The file identifier.
            organisation_id: The organisation ID.
            expires: URL lifetime in seconds. Server default is 900 (15 minutes),
                clamped server-side to [1, 604800] (7 days).

        Raises:
            requests.HTTPError: 404 if the file does not exist, 400 if the id
                refers to a folder.
        """
        params = {"o": str(organisation_id)}
        if expires is not None:
            params["expires"] = str(expires)
        return SignedUrlResponse(**self._make_request("GET", f"/{file_id}/_signed-url", params=params))

    def upload(self, organisation_id: int, file_paths: Sequence[str | Path], path: str = "") -> UploadResult:
        """Upload one or more local files to a logical folder.

        Each file is sent as a multipart "file" field with its content type
        guessed from the filename. Server limits: 100 MiB per file AND per
        request body (multiple files share the body cap). Uploading a name
        that already exists in the folder overwrites it (the previous entry
        is soft-deleted server-side).

        Args:
            organisation_id: The organisation ID.
            file_paths: Local paths of the files to upload.
            path: Destination folder path ("" = root).

        Raises:
            ValueError: If file_paths is empty (before any network call).
        """
        if not file_paths:
            raise ValueError("file_paths must not be empty")
        url = f"{self.base_url}/api/files/_upload"
        params = {"o": str(organisation_id), "path": path}
        with contextlib.ExitStack() as stack:
            files: list[tuple[str, tuple[str, BinaryIO, str | None]]] = []
            for raw_path in file_paths:
                file_path = Path(raw_path)
                handle = stack.enter_context(file_path.open("rb"))
                content_type, _ = mimetypes.guess_type(file_path.name)
                files.append(("file", (file_path.name, handle, content_type)))
            response = requests.post(
                url,
                headers={"Authorization": f"Bearer {self.token_supplier.bearer_token()}"},
                params=params,
                files=files,
                timeout=300,
            )
        response.raise_for_status()
        return UploadResult(**response.json())

    def create_folder(self, organisation_id: int, name: str, folder_path: str = "") -> FileEntry:
        """Create a virtual folder.

        Args:
            organisation_id: The organisation ID.
            name: Name of the new folder. Must not be empty or contain
                '/', '\\' or '..' (server-validated, 400).
            folder_path: Parent folder path ("" = root).

        Raises:
            requests.HTTPError: 409 if a file or folder with that name already
                exists in the parent folder.
        """
        params = {"o": str(organisation_id)}
        data = {"folder_path": folder_path, "name": name}
        return FileEntry(**self._make_request("POST", "/_folder", data=data, params=params))

    def delete_file(self, file_id: str, organisation_id: int) -> None:
        """Soft-delete a single file.

        Raises:
            requests.HTTPError: 404 if the file does not exist, 400 if the id
                refers to a folder (use delete_folder instead).
        """
        params = {"o": str(organisation_id)}
        self._make_request("DELETE", f"/{file_id}", params=params)

    def delete_folder(self, organisation_id: int, path: str) -> None:
        """Recursively soft-delete a virtual folder and all its contents.

        Args:
            organisation_id: The organisation ID.
            path: Full path of the folder to delete.
        """
        params = {"o": str(organisation_id), "path": path}
        self._make_request("DELETE", "/_folder", params=params)
