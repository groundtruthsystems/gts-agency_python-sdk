import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


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
    parameters: dict[str, Any] | None = None


class PromptCollection(BaseModel):
    system: MessageContent
    messages: list[MessageContent]


class PromptContent(BaseModel):
    type: str = "message"
    message: MessageContent | None = None
    collections: PromptCollection | None = None


class PromptPayload(BaseModel):
    name: str
    description: str | None = None
    type: PromptType | None = PromptType.MESSAGE
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None
    content: PromptContent


class Pagination(BaseModel):
    page: int
    size: int


class SearchRequest(BaseModel):
    organisation: int
    tags: list[str] | None = None
    pagination: Pagination | None = None


class PromptsCommand(BaseModel):
    command: str
    organisation: int
    payload: PromptPayload | None = None


class CreatePromptCommand(PromptsCommand):
    command: str = "create"
    payload: PromptPayload


class PromptCommand(BaseModel):
    command: str
    organisation: int
    payload: PromptPayload | None = None


class UpdatePromptCommand(PromptCommand):
    command: str = "update"
    payload: PromptPayload


class PublishPromptCommand(PromptCommand):
    command: str = "publish"
    payload: dict | None = None


class DeletePromptCommand(PromptCommand):
    command: str = "delete"
    payload: dict | None = None


class Page(BaseModel):
    total: int
    page: int
    size: int


class PromptSummary(BaseModel):
    id: str
    name: str
    description: str
    tags: list[str]
    published_version: int | None = None
    unpublished_version: int | None = None
    modified_on: datetime.datetime


class PromptPagedResult(BaseModel):
    page: Page
    items: list[PromptSummary]


class PromptMessageSpecification(BaseModel):
    role: str | None = None
    content_type: str | None = None
    content: str
    parameters: Any | None = None


class PromptCollectionSpecification(BaseModel):
    system: PromptMessageSpecification
    messages: list[PromptMessageSpecification]


class PromptSpecification(BaseModel):
    type_: str = Field(alias="type")
    message: PromptMessageSpecification | None = None
    collections: PromptCollectionSpecification | None = None


class PromptResponse(BaseModel):
    id: str
    name: str
    description: str
    content: PromptSpecification
    tags: Any
    version: int
    status: int
    metadata: dict[str, Any]
    audit: Any