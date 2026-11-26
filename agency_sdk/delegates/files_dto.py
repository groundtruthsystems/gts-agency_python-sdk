"""DTOs for the tenant file storage API (snake_case, matching the API)."""

from typing import Any

import requests
from pydantic import BaseModel, ConfigDict, Field

from agency_sdk.delegates.datasets_dto import Page


class FileEntry(BaseModel):
    """A file or folder as returned by the files API."""

    id: str
    name: str
    folder_path: str
    path: str
    is_folder: bool
    content_type: str | None = None
    size_bytes: int
    uploaded_by: int
    created_on: str


class FilesPagedResult(BaseModel):
    page: Page
    items: list[FileEntry]


class UploadResult(BaseModel):
    uploaded: list[FileEntry]


class SignedUrlResponse(BaseModel):
    """A time-limited signed download URL with the file's metadata."""

    signed_url: str
    expires_at: str
    file: FileEntry


class UploadLimits(BaseModel):
    """Upload limits reported by ``GET /api/files/_limits``.

    Files at or under ``proxied_upload_max_bytes`` go through the proxied ``_upload`` route; larger
    files (up to ``max_upload_bytes``) go through an upload session, transferred in ``part_size`` parts.
    """

    proxied_upload_max_bytes: int
    max_upload_bytes: int
    part_size: int


class UploadPartUrl(BaseModel):
    """A presigned URL for one S3 multipart part. The URL is a credential and is hidden from ``repr``."""

    # Credential-bearing models also keep their input out of validation error messages.
    model_config = ConfigDict(hide_input_in_errors=True)

    part_number: int
    url: str = Field(repr=False)


class UploadSessionResponse(BaseModel):
    """An open upload session for one file.

    ``strategy`` is ``"s3_multipart"`` (``parts``/``parts_expire_at`` set) or ``"gcs_resumable"``
    (``session_uri`` set). ``session_uri`` and part URLs are credentials and are hidden from ``repr``.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    file_id: str
    strategy: str
    part_size: int
    expires_at: str
    limits: UploadLimits
    session_uri: str | None = Field(default=None, repr=False)
    parts: list[UploadPartUrl] | None = Field(default=None, repr=False)
    parts_expire_at: str | None = None


class UploadPartsResponse(BaseModel):
    """Re-signed S3 part URLs from ``POST …/_upload-session/{file_id}/_parts``."""

    model_config = ConfigDict(hide_input_in_errors=True)

    parts: list[UploadPartUrl] = Field(repr=False)
    expires_at: str


class FilesUploadError(requests.HTTPError):
    """An ``upload()`` call stopped part-way. ``uploaded`` holds the entries that already succeeded.

    The original error is the ``__cause__``. Files already uploaded stay uploaded; uploads overwrite
    by name, so retrying the whole call is safe. Subclasses ``requests.HTTPError`` so existing
    ``except requests.HTTPError`` handlers keep working.
    """

    def __init__(self, message: str, *, uploaded: list[FileEntry], response: Any = None) -> None:
        super().__init__(message, response=response)
        self.uploaded = uploaded
