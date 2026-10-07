import datetime
from typing import Any

from pydantic import BaseModel, Field


class Page(BaseModel):
    total: int
    page: int
    size: int


class DatasetVersion(BaseModel):
    version: str
    published: bool
    comment: str | None = None
    created_at: str
    created_by: str


class DatasetSummary(BaseModel):
    id: str
    name: str
    description: str
    tags: Any
    published_version: int | None = None
    unpublished_version: int | None = None
    modified_on: datetime.datetime


class AuditData(BaseModel):
    pass


class DatasetResponse(BaseModel):
    id: str
    name: str
    description: str
    tags: Any
    comment: str
    version: int
    status: int
    metadata: Any
    readme: str
    audit: AuditData


class DatasetFileItem(BaseModel):
    name: str
    item_type: str = Field(alias="type")
    path: str
    size: int | None = None
    content_type: str | None = None
    last_modified: str
    file_count: int | None = None


class DatasetFilesResponse(BaseModel):
    dataset_id: str
    version: str
    path: str
    items: list[DatasetFileItem]


class FileInfo(BaseModel):
    name: str
    path: str
    size: int
    content_type: str
    last_modified: str


class SignedUrlResponse(BaseModel):
    signed_url: str
    expires_at: str
    file_info: FileInfo


class DatasetsPagedResult(BaseModel):
    page: Page
    items: list[DatasetSummary]


class DatasetDraftData(BaseModel):
    dataset_id: str
    version: int
    status: str
    copied_from_version: int | None = None


class DatasetDraftResult(BaseModel):
    """``POST /{id}/_command`` ``draft`` envelope. ``data.status`` is the string ``"draft"`` (stored as ``0``)."""

    success: bool
    message: str
    data: DatasetDraftData


class DatasetPublishData(BaseModel):
    dataset_id: str
    version: int
    status: str


class DatasetPublishResult(BaseModel):
    """``POST /{id}/_command`` ``publish`` envelope. ``data.version`` is the draft just frozen, not a new number."""

    success: bool
    message: str
    data: DatasetPublishData


class DatasetManifestEntry(BaseModel):
    """One ``.agency/files.json`` entry. Only ``hash`` drives change detection; ``last_modified`` is recorded."""

    hash: str
    last_modified: str


class DatasetPushResult(BaseModel):
    """What a push did. Paths are tree-relative, forward-slashed."""

    dataset_id: str
    version: int
    uploaded: list[str] = Field(default_factory=list)
    deleted: list[str] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.uploaded and not self.deleted
