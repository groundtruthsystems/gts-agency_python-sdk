"""DTOs for the session-templates API (`/api/session_templates`, snake_case).

Used to resolve a session-template NAME → id (the caller matches by name); the
list is Page-wrapped like the other paged reads.
"""

from typing import Any

from pydantic import BaseModel

from agency_sdk.delegates.datasets_dto import Page


class SessionTemplateResponse(BaseModel):
    """A session template as returned by ``GET /api/session_templates``."""

    id: str
    name: str
    organisation_id: int
    type: str | None = None
    executed: int | None = None
    audit_data: dict[str, Any] | None = None


class SessionTemplatesPagedResult(BaseModel):
    page: Page
    items: list[SessionTemplateResponse]
