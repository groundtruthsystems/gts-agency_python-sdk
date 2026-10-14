"""DTOs for the session-reporting delegate.

- ``SessionStatus`` encodes the control-plane status int map the agent reports
  (``failed`` → -1, completed-with-result → 0, in-progress → 2). The
  pipeline-outcome → status DECISION lives in the agent; the SDK only marshals
  the caller-chosen value.
- ``SessionCommandResponse`` is transcribed from the server's ``_command``
  response schema (``success``/``message`` + an optional command sub-result),
  per the "transcribe an EXISTING endpoint, never assert" rule.
- ``AnalyticsEvent`` is the cross-agent shared event shape (the fixed
  ``%Y-%m-%d %H:%M:%S`` serialization) promoted into the SDK so every agent
  emits one shape.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import IntEnum
from typing import Any

from pydantic import BaseModel, Field, field_serializer

from agency_sdk.delegates.datasets_dto import Page


class SessionStatus(IntEnum):
    """The control-plane session status codes an agent reports via ``update``."""

    FAILED = -1
    COMPLETED = 0
    IN_PROGRESS = 2
    #: Non-terminal: the run paused for a human answer recorded in comand (Agent Vault). The code is
    #: provisional until the backend track confirms it; comand sets it when a HITL request is created.
    AWAITING_INPUT = 3


class SessionCommandResponse(BaseModel):
    """The server's response to a session ``_command`` (e.g. ``update``).

    ``update`` carries the command-specific sub-result when present; it is left
    as an opaque dict because the server marks every field of it optional.
    """

    success: bool
    message: str
    update: dict[str, Any] | None = None


class SessionRef(BaseModel):
    """A session located by correlation-id lookup or label search: its id, status and labels."""

    session_id: str
    status: int
    labels: dict[str, str] = Field(default_factory=dict)


class SessionSearchResult(BaseModel):
    """One page of ``POST /api/sessions/_search`` results (policy-filtered server-side)."""

    page: Page
    items: list[SessionRef]


class AnalyticsEvent(BaseModel):
    """Platform-wide analytics event — the cross-agent "sibling-contract" shape.

    The field set and the ``%Y-%m-%d %H:%M:%S`` timestamp serialization are the
    shape every agent emits so control-plane consumers parse one format.
    """

    id: str | None
    correlation: str
    event_type: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int | None = None
    step_name: str | None = None
    workflow_name: str | None = None

    @field_serializer("timestamp")
    def serialize_timestamp(self, value: datetime) -> str:
        return value.strftime("%Y-%m-%d %H:%M:%S")

    @classmethod
    def new(cls, correlation: str, event_type: str, payload: dict[str, Any] | None = None) -> AnalyticsEvent:
        """Create an event with a fresh id and default timestamp."""
        return cls(
            id=str(uuid.uuid4()),
            correlation=correlation,
            event_type=event_type,
            payload=payload if payload is not None else {},
        )

    @classmethod
    def step_event(
        cls,
        correlation: str,
        event_type: str,
        step_name: str,
        workflow_name: str | None = None,
        duration_ms: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AnalyticsEvent:
        """Create a step-scoped event (carries step/workflow/duration metadata)."""
        return cls(
            id=str(uuid.uuid4()),
            correlation=correlation,
            event_type=event_type,
            payload=payload if payload is not None else {},
            duration_ms=duration_ms,
            step_name=step_name,
            workflow_name=workflow_name,
        )
