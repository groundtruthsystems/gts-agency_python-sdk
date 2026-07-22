"""Client for the work-queue ingestion API (/api/work_queues).

Mirrors the Track ① files-inbox ingestion contract (design §5/§6). Work items
(cards) are claimed exactly-once via external refs under a **queue-scoped** UNIQUE
key (① ``55f9f1f5``), so on ``create_item`` and ``add_ref`` an HTTP 409 is a normal
claim-lost outcome — another card in the SAME queue already owns the ref — not an
error. Both methods catch it and return a typed result carrying the owner's summary;
every other HTTP error propagates as usual.
"""

from __future__ import annotations

import builtins  # the `list()` method shadows the builtin, so annotations use builtins.list[...]
from collections.abc import Mapping, Sequence
from typing import Any

import requests

from agency_sdk.delegates.base_client import BaseDelegateClient
from agency_sdk.delegates.work_queue_dto import (
    AddRefResult,
    CreateItemResult,
    ExistingItemSummary,
    ItemCommandResponse,
    ItemResponse,
    QueuesPagedResult,
)


#: The ``error.type`` an owner-less "claim contended" 409 carries (the ①-side
#: ``AppError::Conflict(String)`` fallback). It is the deterministic signal for a
#: retryable conflict with no owner — never inferred from missing ``details``.
_CONFLICT_RETRY_TYPE = "CONFLICT_RETRY"


def _parse_conflict(error: requests.HTTPError) -> tuple[dict[str, Any] | None, bool] | None:
    """Parse a 409 from the standard error envelope ``{error: {message, type, details}}``.

    Returns ``(owner_details, contended)``:

    - owner conflict → ``(details, False)`` where ``details`` is the owning card's summary.
    - owner-less ``CONFLICT_RETRY`` → ``(None, True)``.

    Returns ``None`` when it is not a usable conflict — a non-409 status, a body that
    will not parse as JSON, or an envelope 409 that is neither an owner-details conflict
    nor ``CONFLICT_RETRY`` — so the caller re-raises the original ``HTTPError`` rather
    than fabricating a claim-lost result.
    """
    response = error.response
    if response is None or response.status_code != 409:
        return None
    try:
        body = response.json()
    except ValueError:  # includes requests.JSONDecodeError (malformed/empty 409 body)
        return None
    if not isinstance(body, dict):
        return None
    error_obj = body.get("error")
    if not isinstance(error_obj, dict):
        return None
    details = error_obj.get("details")
    if isinstance(details, dict) and "work_item_id" in details:
        return details, False
    if error_obj.get("type") == _CONFLICT_RETRY_TYPE:
        return None, True
    return None


class AgencyWorkQueueClient(BaseDelegateClient):
    api_path = "/api/work_queues"

    def list(self, organisation_id: int, *, page: int = 0, size: int = 50) -> QueuesPagedResult:
        """List the org's work queues (paged), for resolving a queue NAME → id.

        Name→id matching (and any cache / not-found / duplicate handling) is the
        caller's job; this returns the raw page.
        """
        params = {"o": str(organisation_id), "p": str(page), "s": str(size)}
        return QueuesPagedResult(**self._make_request("GET", "", params=params))

    def create_item(
        self,
        queue_id: int,
        organisation_id: int,
        *,
        title: str,
        session_template_id: str,
        input_data: dict[str, Any],
        external_refs: Sequence[Mapping[str, str]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CreateItemResult:
        """Create a work item, atomically claiming its external refs.

        The server inserts the card and its refs in one transaction; a
        duplicate ref rolls the whole transaction back (no card is created)
        and returns 409 with the owning card's summary.

        Args:
            queue_id: The work queue ID.
            organisation_id: The organisation ID.
            title: Card title.
            session_template_id: Session template dispatched on publish.
                Ingestion items must be template-based (the agent_id branch
                dispatches no session).
            input_data: Arbitrary JSON object stored on the card and passed
                to the dispatched session.
            external_refs: Identity claims, each ``{"ref_type": ...,
                "ref_value": ...}`` (e.g. file_id / content_hash). Unique
                per queue per (ref_type, ref_value).
            metadata: Optional informational JSON (filename, folder, ...).

        Returns:
            ``created=True`` with the new item; ``created=False`` with
            ``existing`` identifying the card that already owns a ref; or
            ``created=False, contended=True`` (no ``existing``) for the
            owner-less ``CONFLICT_RETRY`` fallback — a transient claim race the
            caller may retry.
        """
        data: dict[str, Any] = {
            "title": title,
            "session_template_id": session_template_id,
            "input_data": input_data,
        }
        if external_refs is not None:
            data["external_refs"] = [dict(ref) for ref in external_refs]
        if metadata is not None:
            data["metadata"] = metadata
        try:
            body = self._make_request("POST", f"/{queue_id}/items", data=data, params={"o": str(organisation_id)})
        except requests.HTTPError as error:
            conflict = _parse_conflict(error)
            if conflict is None:
                raise
            details, contended = conflict
            existing = ExistingItemSummary(**details) if details is not None else None
            return CreateItemResult(created=False, existing=existing, contended=contended)
        return CreateItemResult(created=True, item=ItemResponse(**body))

    def add_ref(
        self, queue_id: int, item_id: int, organisation_id: int, *, ref_type: str, ref_value: str
    ) -> AddRefResult:
        """Claim an external ref for an existing item (content-layer claim).

        Returns:
            ``added=True`` when the ref was inserted; ``added=False`` with the
            owning card's id and status when another card already holds the ref
            (HTTP 409); or ``added=False, contended=True`` (no owner) for the
            owner-less ``CONFLICT_RETRY`` fallback (retryable).
        """
        data = {"command": "add_ref", "ref_type": ref_type, "ref_value": ref_value}
        try:
            self._make_request(
                "POST", f"/{queue_id}/items/{item_id}/_command", data=data, params={"o": str(organisation_id)}
            )
        except requests.HTTPError as error:
            conflict = _parse_conflict(error)
            if conflict is None:
                raise
            details, contended = conflict
            if details is not None:
                return AddRefResult(
                    added=False, owner_work_item_id=details["work_item_id"], owner_status=details["status"]
                )
            return AddRefResult(added=False, contended=contended)
        return AddRefResult(added=True)

    def publish_item(self, queue_id: int, item_id: int, organisation_id: int) -> ItemCommandResponse:
        """Publish a backlog item, dispatching its session (injects work_item_id).

        Returns:
            The command outcome; ``session_id`` identifies the dispatched
            session. Fetch the updated card via get_item if needed.

        Raises:
            requests.HTTPError: 400 if the item is not backlog+unpublished or
                its input_data is not a JSON object.
        """
        return self.item_command(queue_id, item_id, organisation_id, "publish")

    def item_command(
        self, queue_id: int, item_id: int, organisation_id: int, command: str, **kwargs: Any
    ) -> ItemCommandResponse:
        """Run an item command (publish / unblock / retry / reprocess).

        Extra keyword arguments are passed through as flat siblings of
        ``command`` in the request body (e.g. ``feedback=...`` for unblock).
        Commands report their outcome — they do not return the updated item
        (GET it if needed). Unlike create_item/add_ref, a 409 here is a
        genuine error and propagates.
        """
        data = {"command": command, **kwargs}
        body = self._make_request(
            "POST", f"/{queue_id}/items/{item_id}/_command", data=data, params={"o": str(organisation_id)}
        )
        return ItemCommandResponse(**body)

    def get_item(self, queue_id: int, item_id: int, organisation_id: int) -> ItemResponse:
        """Fetch a single work item."""
        body = self._make_request("GET", f"/{queue_id}/items/{item_id}", params={"o": str(organisation_id)})
        return ItemResponse(**body)

    def get_items_by_ref(
        self,
        organisation_id: int,
        *,
        queue_id: int | None = None,
        ref_type: str,
        ref_value: str,
    ) -> builtins.list[ItemResponse]:
        """List the items holding an external ref — queue-scoped owner lookup.

        The dedicated ``_by_ref`` route was merged into the paginated ``/items``: passing
        ``ref_type`` + ``ref_value`` turns ``/items`` into the owner lookup. ``queue_id=None``
        scopes to the whole org (``_``); a queue id scopes to that queue. A given
        ``(ref_type, ref_value)`` may be held by one card **per queue**, so this returns a
        list; an empty list means no card holds the ref.

        Returns:
            The owning items (possibly empty — a miss is an empty page, not a 404; a genuine
            404 propagates). Owner count is bounded by one card per queue, so a single large
            page (``s=1000``) returns them all.
        """
        scope = "_" if queue_id is None else str(queue_id)
        params = {"o": str(organisation_id), "ref_type": ref_type, "ref_value": ref_value, "s": "1000"}
        body = self._make_request("GET", f"/{scope}/items", params=params)
        return [ItemResponse(**item) for item in body["items"]]

    def delete_item(self, queue_id: int, item_id: int, organisation_id: int) -> None:
        """Hard-delete an item — the "full forget": its external refs CASCADE away with it."""
        self._make_request("DELETE", f"/{queue_id}/items/{item_id}", params={"o": str(organisation_id)})
