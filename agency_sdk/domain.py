import datetime
from datetime import UTC
from enum import Enum
from typing import Optional, List, Dict, Any

from pydantic.class_validators import validator
from pydantic.main import BaseModel
from pydantic import Field


class PromptType(Enum):
    MESSAGE = 0


class MessageRole(Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class ContentType(Enum):
    TEXT = "text"


class MessageContent(BaseModel):
    role: MessageRole
    content_type: ContentType
    content: str
    parameters: Optional[Dict[str, Any]] = None


class PromptCollection(BaseModel):
    system: MessageContent
    messages: list[MessageContent]


class PromptContent(BaseModel):
    type: str = "message"
    message: Optional[MessageContent] = None
    collections: Optional[PromptCollection] = None


class PromptPayload(BaseModel):
    name: str
    description: Optional[str] = None
    type: Optional[PromptType] = PromptType.MESSAGE
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None
    content: PromptContent

class Pagination(BaseModel):
    page: int
    size: int

class SearchRequest(BaseModel):
    organisation: int
    tags: Optional[list[str]]
    pagination: Optional[Pagination]


class PromptsCommand(BaseModel):
    command: str
    organisation: int
    payload: Optional[PromptPayload] = None


class CreatePromptCommand(PromptsCommand):
    command: str = "create"
    payload: PromptPayload


class PromptCommand(BaseModel):
    command: str
    organisation: int
    payload: Optional[PromptPayload] = None

class UpdatePromptCommand(PromptCommand):
    command: str = "update"
    payload: PromptPayload


class PublishPromptCommand(PromptCommand):
    command: str = "publish"
    payload: Optional[Dict] = None



class DeletePromptCommand(PromptCommand):
    command: str = "delete"
    payload: Optional[Dict] = None


class PromptSummary(BaseModel):
    id: str
    name: str
    description: str
    tags: list[str]
    published_version: Optional[int] = None
    unpublished_version: Optional[int] = None
    modified_on: datetime.datetime


class Page(BaseModel):
    total: int
    page: int
    size: int

class PromptPagedResult(BaseModel):
    page: Page
    items: list[PromptSummary]



class PromptMessageSpecification(BaseModel):
    role: str = None
    content_type: Optional[str] = None
    content: str
    parameters: Optional[Any] = None  # Equivalent to Option<Value>

class PromptCollectionSpecification(BaseModel):
    system: PromptMessageSpecification
    messages: List[PromptMessageSpecification]


class PromptSpecification(BaseModel):
    type_: str = Field(alias="type")  # Handle Rust's r#type
    message: Optional[PromptMessageSpecification] = None
    collections: Optional[PromptCollectionSpecification] = None


class PromptResponse(BaseModel):
    id: str
    name: str
    description: str
    content: PromptSpecification  # Equivalent to serde_json::Value
    tags: Any  # Equivalent to serde_json::Value
    version: int  # i64 maps to int
    status: int  # i64 maps to int
    metadata: Dict[str, Any]  # HashMap<String, Value>
    audit: Any