"""DTOs for the work-queue ingestion API (snake_case, matching the API).

Mirrors the Track ① contract (files-inbox-ingestion design §5/§6):

- ``ItemResponse`` matches the server's work-queue item DTO
  (gts-agency-control ``work_queue_dto.rs``), which omits nullable fields
  entirely when null — every optional field therefore defaults to ``None``.
- ``CreateItemResult`` / ``AddRefResult`` wrap the 201-vs-409 outcomes of
  ``create_item`` / ``add_ref``: a 409 is a normal claim-lost outcome carrying
  the owning card's summary, not an error. Note the two 409 bodies differ —
  create's carries ``{work_item_id, status, published}``, add_ref's only
  ``{work_item_id, status}``.
"""

from typing import Any

from pydantic import BaseModel


class DependencyResponse(BaseModel):
    """A dependency edge as embedded in an item response."""

    depends_on_item_id: int
    depends_on_title: str
    depends_on_status: str
    created_on: str


class ItemResponse(BaseModel):
    """A work-queue item (card) as returned by the work-queue API."""

    id: int
    work_queue_id: int
    title: str
    description: str | None = None
    status: str
    published: bool
    priority: int
    session_template_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    assigned_worker_id: str | None = None
    input_data: Any = None
    saved_state: Any = None
    result_data: Any = None
    metadata: Any = None
    dependencies: list[DependencyResponse] | None = None
    started_on: str | None = None
    blocked_on: str | None = None
    unblocked_on: str | None = None
    completed_on: str | None = None
    created_on: str
    modified_on: str


class ItemCommandResponse(BaseModel):
    """Outcome of an item command (publish / unblock / retry / reprocess).

    The ``_command`` endpoint's established server shape (transcribed from
    ``work_queue_dto.rs``, per design §6.0): commands report what happened —
    they do not return the updated item (GET it if needed). ``session_id`` is
    only present for commands that dispatched a session (e.g. publish).
    """

    success: bool
    message: str
    session_id: str | None = None


class ExistingItemSummary(BaseModel):
    """The owning card's summary carried by create_item's 409 body."""

    work_item_id: int
    status: str
    published: bool


class CreateItemResult(BaseModel):
    """Outcome of create_item: 201 → created item, 409 → the existing owner.

    On a 409 the whole server transaction rolled back, so no card was created;
    ``existing`` identifies the card that already owns the external ref.
    """

    created: bool
    item: ItemResponse | None = None
    existing: ExistingItemSummary | None = None


class AddRefResult(BaseModel):
    """Outcome of add_ref: 201 → ref added, 409 → another card owns the ref."""

    added: bool
    owner_work_item_id: int | None = None
    owner_status: str | None = None
