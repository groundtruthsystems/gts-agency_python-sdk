"""DTOs for the session-templates API (`/api/session_templates`, snake_case).

Used to resolve a session-template NAME → id (the caller matches by name); the
list is Page-wrapped like the other paged reads.
"""

from typing import Any

from pydantic import BaseModel, Field

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


class RegisteredSession(BaseModel):
    """The ``register`` sub-result: the new session, the task it works on, and its server-set owner.

    ``correlation_id`` is the caller's ``input.correlation`` or, without one, the session id.
    ``user_id`` (PII) is the registering principal and stays out of ``repr``.
    """

    session_id: str
    correlation_id: str
    user_id: str = Field(repr=False)


class ResolvedSession(BaseModel):
    """The ``resolve_session`` sub-result.

    ``created`` is true when this call created the session or attached the correlation id to the given
    dispatched session; false when it found an existing one (a live session first, else the newest
    finished one).
    """

    session_id: str
    created: bool
    status: int
    vault_version: int
