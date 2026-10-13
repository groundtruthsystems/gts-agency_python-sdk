"""DTOs for the session HITL API (``/api/sessions/{id}/hitl``, snake_case).

A human-input request is a comand resource: the agent creates it when a run pauses on a long-running
tool call, a human principal answers it, and comand records the verified ``responder``. ``prompt``
(the tool call's arguments) and ``response`` are PHI-capable, so ``repr``/``str`` never show them.
"""

from typing import Any, Literal

from pydantic import BaseModel

HitlStatus = Literal["pending", "answered", "expired"]


class HitlRequest(BaseModel):
    """One human-input request. ``request_id`` is the ADK function-call id it answers."""

    request_id: str
    invocation_id: str
    status: HitlStatus
    #: ``{"tool": <name>, "args": {...}}`` — PHI-capable; stored ``confidential`` by comand.
    prompt: dict[str, Any] | None = None
    #: The human's answer — PHI-capable.
    response: Any = None
    #: The authenticated human who answered (recorded by comand, never by a client).
    responder: str | None = None
    answered_at: str | None = None
    expires_at: str
    created_at: str | None = None

    def __repr__(self) -> str:
        return f"HitlRequest(request_id={self.request_id!r}, status={self.status!r})"

    def __str__(self) -> str:
        return repr(self)


class HitlRequestList(BaseModel):
    items: list[HitlRequest]
