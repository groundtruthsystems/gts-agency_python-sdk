import datetime
from datetime import UTC
from enum import Enum
from typing import Optional, List, Any

from pydantic.class_validators import validator
from pydantic.main import BaseModel
from pydantic import Field

class Page(BaseModel):
    total: int
    page: int
    size: int

class DatasetVersion(BaseModel):
    version: str
    published: bool
    comment: Optional[str] = None
    created_at: str
    created_by: str


class DatasetSummary(BaseModel):
    id: str
    name: str
    description: str
    tags: Any
    published_version: Optional[int] = None
    unpublished_version: Optional[int] = None
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
    size: Optional[int] = None
    content_type: Optional[str] = None
    last_modified: str
    file_count: Optional[int] = None


class DatasetFilesResponse(BaseModel):
    dataset_id: str
    version: str
    path: str
    items: List[DatasetFileItem]

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
    items: List[DatasetSummary]
