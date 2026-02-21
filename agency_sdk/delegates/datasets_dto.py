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