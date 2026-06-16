"""DTOs for the tenant file storage API (snake_case, matching the API)."""

from pydantic import BaseModel

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
