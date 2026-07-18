"""Client for the work-queue ingestion API (/api/work_queues).

Mirrors the Track ① files-inbox ingestion contract (design §5/§6). Work items
(cards) are claimed exactly-once via external refs under an org-wide UNIQUE
key, so on ``create_item`` and ``add_ref`` an HTTP 409 is a normal
claim-lost outcome — another card already owns the ref — not an error. Both
methods catch it and return a typed result carrying the owner's summary;
every other HTTP error propagates as usual.
"""

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
)


def _conflict_body(error: requests.HTTPError) -> dict[str, Any] | None:
    """Return the parsed 409 body, or None when the error is not a conflict."""
    response = error.response
    if response is None or response.status_code != 409:
        return None
    body: dict[str, Any] = response.json()
    return body


class AgencyWorkQueueClient(BaseDelegateClient):
    api_path = "/api/work_queues"

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
                org-wide per (ref_type, ref_value).
            metadata: Optional informational JSON (filename, folder, ...).

        Returns:
            ``created=True`` with the new item, or ``created=False`` with
            ``existing`` identifying the card that already owns a ref.
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
            conflict = _conflict_body(error)
            if conflict is None:
                raise
            return CreateItemResult(created=False, existing=ExistingItemSummary(**conflict))
        return CreateItemResult(created=True, item=ItemResponse(**body))

    def add_ref(
        self, queue_id: int, item_id: int, organisation_id: int, *, ref_type: str, ref_value: str
    ) -> AddRefResult:
        """Claim an external ref for an existing item (content-layer claim).

        Returns:
            ``added=True`` when the ref was inserted, or ``added=False`` with
            the owning card's id and status when another card already holds
            the ref (HTTP 409).
        """
        data = {"command": "add_ref", "ref_type": ref_type, "ref_value": ref_value}
        try:
            self._make_request(
                "POST", f"/{queue_id}/items/{item_id}/_command", data=data, params={"o": str(organisation_id)}
            )
        except requests.HTTPError as error:
            conflict = _conflict_body(error)
            if conflict is None:
                raise
            return AddRefResult(
                added=False, owner_work_item_id=conflict["work_item_id"], owner_status=conflict["status"]
            )
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

    def get_item_by_ref(self, organisation_id: int, *, ref_type: str, ref_value: str) -> ItemResponse | None:
        """Look up the item owning an external ref — org-scoped, not queue-scoped.

        The server UNIQUE key is org-wide, so the owning card may live in any
        queue of the organisation.

        Returns:
            The owning item, or None when no card holds the ref (HTTP 404).
        """
        params = {"o": str(organisation_id), "ref_type": ref_type, "ref_value": ref_value}
        try:
            body = self._make_request("GET", "/items/_by_ref", params=params)
        except requests.HTTPError as error:
            if error.response is not None and error.response.status_code == 404:
                return None
            raise
        return ItemResponse(**body)

    def delete_item(self, queue_id: int, item_id: int, organisation_id: int) -> None:
        """Hard-delete an item — the "full forget": its external refs CASCADE away with it."""
        self._make_request("DELETE", f"/{queue_id}/items/{item_id}", params={"o": str(organisation_id)})
