"""Offline protocol tests for AgencyWorkQueueClient.

Every test asserts the exact wire contract of the Track ① work-queue ingestion
surface: `?o={org}` on every call, the flat `{"command": ...}` envelope, and —
the load-bearing behaviour — 409 as control flow: `create_item` / `add_ref`
catch the HTTPError and return a typed result (`created=False` / `added=False`
+ the owner fields), never re-raise. The 409 body is the standard error
envelope `{"error": {"message", "type", "details"}}`: `error.details` carries
the owner summary; `error.type == "CONFLICT_RETRY"` is the owner-less contended
fallback (→ `contended=True`).
"""

import pytest
import requests

from agency_sdk.delegates.work_queue_client import AgencyWorkQueueClient
from agency_sdk.delegates.work_queue_dto import CreateItemResult, ItemCommandResponse, ItemResponse
from agency_sdk.test.test_work_queue_dto import CREATE_CONFLICT_JSON, ITEM_JSON, QUEUE_JSON

# Owner summary carried inside error.details (add_ref's is narrower — no `published`).
ADD_REF_CONFLICT_JSON = {"work_item_id": 4711, "status": "blocked"}

# The 409 wire bodies: standard error envelope. An owner conflict carries error.details;
# the owner-less fallback carries error.type == "CONFLICT_RETRY" and no details.
CREATE_CONFLICT_ENVELOPE = {
    "error": {"message": "ref already claimed", "type": "CONFLICT", "details": CREATE_CONFLICT_JSON}
}
ADD_REF_CONFLICT_ENVELOPE = {
    "error": {"message": "ref already claimed", "type": "CONFLICT", "details": ADD_REF_CONFLICT_JSON}
}
CONFLICT_RETRY_ENVELOPE = {"error": {"message": "claim contended, retry", "type": "CONFLICT_RETRY"}}

# The _command endpoint's real response shape (server ItemCommandResponse); session_id is
# omitted entirely for commands that dispatch no session.
PUBLISH_OK_JSON = {"success": True, "message": "Item published successfully", "session_id": "sess-0001"}


@pytest.fixture
def client(fake_credentials):
    return AgencyWorkQueueClient(token_supplier=fake_credentials, base_url="http://cp.test/")


class TestCreateItem:
    def test_create_item_posts_body_and_wraps_created_item(self, client, stub_requests):
        stub_requests.queue(json_data=ITEM_JSON, status_code=201)

        result = client.create_item(
            queue_id=12,
            organisation_id=2,
            title="Ingest inbox/report.pdf",
            session_template_id="guideline-extraction",
            input_data={"file_id": "file_550e8400", "path": "inbox/report.pdf"},
            external_refs=[
                {"ref_type": "file_id", "ref_value": "file_550e8400"},
                {"ref_type": "content_hash", "ref_value": "deadbeef"},
            ],
            metadata={"filename": "report.pdf"},
        )

        call = stub_requests.calls[0]
        assert call.method == "POST"
        assert call.url == "http://cp.test/api/work_queues/12/items"
        assert call.kwargs["params"] == {"o": "2"}
        assert call.kwargs["headers"]["Authorization"] == "Bearer test-token"
        assert call.kwargs["json"] == {
            "title": "Ingest inbox/report.pdf",
            "session_template_id": "guideline-extraction",
            "input_data": {"file_id": "file_550e8400", "path": "inbox/report.pdf"},
            "external_refs": [
                {"ref_type": "file_id", "ref_value": "file_550e8400"},
                {"ref_type": "content_hash", "ref_value": "deadbeef"},
            ],
            "metadata": {"filename": "report.pdf"},
        }
        assert isinstance(result, CreateItemResult)
        assert result.created is True
        assert result.item is not None
        assert result.item.id == ITEM_JSON["id"]
        assert result.existing is None

    def test_create_item_omits_optional_body_fields_when_absent(self, client, stub_requests):
        stub_requests.queue(json_data=ITEM_JSON, status_code=201)

        client.create_item(
            queue_id=12,
            organisation_id=2,
            title="Ingest inbox/report.pdf",
            session_template_id="guideline-extraction",
            input_data={"file_id": "file_550e8400"},
        )

        body = stub_requests.calls[0].kwargs["json"]
        assert "external_refs" not in body
        assert "metadata" not in body

    def test_create_item_conflict_returns_existing_summary_not_raise(self, client, stub_requests):
        # Owner conflict: the owner summary comes from error.details.
        stub_requests.queue(json_data=CREATE_CONFLICT_ENVELOPE, status_code=409)

        result = client.create_item(
            queue_id=12,
            organisation_id=2,
            title="Ingest inbox/report.pdf",
            session_template_id="guideline-extraction",
            input_data={"file_id": "file_550e8400"},
            external_refs=[{"ref_type": "file_id", "ref_value": "file_550e8400"}],
        )

        assert result.created is False
        assert result.item is None
        assert result.contended is False
        assert result.existing is not None
        assert result.existing.work_item_id == 4711
        assert result.existing.status == "doing"
        assert result.existing.published is True

    def test_create_item_conflict_retry_sets_contended_without_owner(self, client, stub_requests):
        # Owner-less fallback: error.type == CONFLICT_RETRY, no details.
        stub_requests.queue(json_data=CONFLICT_RETRY_ENVELOPE, status_code=409)

        result = client.create_item(
            queue_id=12,
            organisation_id=2,
            title="dup",
            session_template_id="st",
            input_data={},
            external_refs=[{"ref_type": "file_id", "ref_value": "f1"}],
        )

        assert result.created is False
        assert result.existing is None
        assert result.contended is True

    def test_create_item_envelope_409_without_owner_or_retry_reraises(self, client, stub_requests):
        # An enveloped 409 that is neither an owner conflict nor CONFLICT_RETRY is unexpected —
        # surface the original HTTPError rather than fabricate a claim-lost result.
        stub_requests.queue(json_data={"error": {"message": "weird", "type": "SOMETHING_ELSE"}}, status_code=409)

        with pytest.raises(requests.HTTPError):
            client.create_item(queue_id=12, organisation_id=2, title="t", session_template_id="st", input_data={})

    @pytest.mark.parametrize("body", [["not", "a", "dict"], {"error": "just a string"}, {"nope": 1}])
    def test_create_item_non_envelope_409_reraises(self, client, stub_requests, body):
        # A 409 that is not the standard {error:{...}} envelope (non-dict body, non-dict error,
        # or a dict without `error`) is unexpected → re-raise the original HTTPError.
        stub_requests.queue(json_data=body, status_code=409)

        with pytest.raises(requests.HTTPError):
            client.create_item(queue_id=12, organisation_id=2, title="t", session_template_id="st", input_data={})

    def test_create_item_propagates_non_conflict_errors(self, client, stub_requests):
        stub_requests.queue(json_data={"message": "Organisation not specified."}, status_code=400)

        with pytest.raises(requests.HTTPError):
            client.create_item(
                queue_id=12,
                organisation_id=2,
                title="t",
                session_template_id="st",
                input_data={},
            )

    def test_create_item_malformed_409_body_reraises_original_httperror(self, client, stub_requests):
        # A 409 whose body will not parse as JSON is unexpected — surface the original
        # HTTPError, not a JSONDecodeError, and never fabricate a claim-lost result.
        response = stub_requests.queue(status_code=409)

        def _no_json():
            raise ValueError("No JSON could be decoded")

        response.json = _no_json  # type: ignore[method-assign]

        with pytest.raises(requests.HTTPError):
            client.create_item(
                queue_id=12,
                organisation_id=2,
                title="dup",
                session_template_id="st",
                input_data={},
                external_refs=[{"ref_type": "file_id", "ref_value": "f1"}],
            )


class TestAddRef:
    def test_add_ref_posts_flat_command_envelope(self, client, stub_requests):
        stub_requests.queue(json_data={"success": True}, status_code=201)

        result = client.add_ref(
            queue_id=12, item_id=4711, organisation_id=2, ref_type="content_hash", ref_value="deadbeef"
        )

        call = stub_requests.calls[0]
        assert call.method == "POST"
        assert call.url == "http://cp.test/api/work_queues/12/items/4711/_command"
        assert call.kwargs["params"] == {"o": "2"}
        assert call.kwargs["json"] == {"command": "add_ref", "ref_type": "content_hash", "ref_value": "deadbeef"}
        assert result.added is True
        assert result.owner_work_item_id is None
        assert result.owner_status is None

    def test_add_ref_conflict_returns_owner_not_raise(self, client, stub_requests):
        # Owner conflict: owner from error.details.
        stub_requests.queue(json_data=ADD_REF_CONFLICT_ENVELOPE, status_code=409)

        result = client.add_ref(
            queue_id=12, item_id=4712, organisation_id=2, ref_type="content_hash", ref_value="deadbeef"
        )

        assert result.added is False
        assert result.owner_work_item_id == 4711
        assert result.owner_status == "blocked"
        assert result.contended is False

    def test_add_ref_conflict_retry_sets_contended_without_owner(self, client, stub_requests):
        # Owner-less fallback: error.type == CONFLICT_RETRY.
        stub_requests.queue(json_data=CONFLICT_RETRY_ENVELOPE, status_code=409)

        result = client.add_ref(
            queue_id=12, item_id=4712, organisation_id=2, ref_type="content_hash", ref_value="deadbeef"
        )

        assert result.added is False
        assert result.owner_work_item_id is None
        assert result.owner_status is None
        assert result.contended is True

    def test_add_ref_propagates_non_conflict_errors(self, client, stub_requests):
        stub_requests.queue(json_data={"message": "boom"}, status_code=500)

        with pytest.raises(requests.HTTPError):
            client.add_ref(queue_id=12, item_id=4711, organisation_id=2, ref_type="file_id", ref_value="x")


class TestPublishItem:
    def test_publish_item_sends_publish_command_and_returns_command_response(self, client, stub_requests):
        stub_requests.queue(json_data=PUBLISH_OK_JSON)

        result = client.publish_item(queue_id=12, item_id=4711, organisation_id=2)

        call = stub_requests.calls[0]
        assert call.method == "POST"
        assert call.url == "http://cp.test/api/work_queues/12/items/4711/_command"
        assert call.kwargs["params"] == {"o": "2"}
        assert call.kwargs["json"] == {"command": "publish"}
        assert isinstance(result, ItemCommandResponse)
        assert result.success is True
        assert result.message == "Item published successfully"
        assert result.session_id == "sess-0001"

    def test_publish_item_propagates_precondition_errors(self, client, stub_requests):
        # publish hard-rejects anything not backlog+unpublished — that is an error, not control flow
        stub_requests.queue(json_data={"message": "not in backlog"}, status_code=400)

        with pytest.raises(requests.HTTPError):
            client.publish_item(queue_id=12, item_id=4711, organisation_id=2)


class TestItemCommand:
    @pytest.mark.parametrize("command", ["unblock", "retry", "reprocess"])
    def test_item_command_passes_command_through(self, client, stub_requests, command):
        # non-dispatching command responses carry no session_id key at all
        stub_requests.queue(json_data={"success": True, "message": f"Item {command} ok"})

        result = client.item_command(queue_id=12, item_id=4711, organisation_id=2, command=command)

        call = stub_requests.calls[0]
        assert call.url == "http://cp.test/api/work_queues/12/items/4711/_command"
        assert call.kwargs["json"] == {"command": command}
        assert isinstance(result, ItemCommandResponse)
        assert result.success is True
        assert result.message == f"Item {command} ok"
        assert result.session_id is None

    def test_item_command_forwards_extra_fields(self, client, stub_requests):
        stub_requests.queue(json_data={"success": True, "message": "Item unblocked"})

        client.item_command(queue_id=12, item_id=4711, organisation_id=2, command="unblock", feedback="fixed creds")

        assert stub_requests.calls[0].kwargs["json"] == {"command": "unblock", "feedback": "fixed creds"}

    def test_item_command_propagates_conflicts(self, client, stub_requests):
        # only create_item/add_ref treat 409 as control flow; commands do not
        stub_requests.queue(json_data={"message": "conflict"}, status_code=409)

        with pytest.raises(requests.HTTPError):
            client.item_command(queue_id=12, item_id=4711, organisation_id=2, command="retry")


class TestGetItem:
    def test_get_item_hits_item_endpoint(self, client, stub_requests):
        stub_requests.queue(json_data=ITEM_JSON)

        result = client.get_item(queue_id=12, item_id=4711, organisation_id=2)

        call = stub_requests.calls[0]
        assert call.method == "GET"
        assert call.url == "http://cp.test/api/work_queues/12/items/4711"
        assert call.kwargs["params"] == {"o": "2"}
        assert result.id == 4711
        assert result.status == "backlog"


class TestGetItemsByRef:
    def test_org_scope_uses_underscore_path_and_returns_list(self, client, stub_requests):
        # queue_id=None → the whole-org scope `_`; ref_type/ref_value turn the paged /items into an
        # owner lookup. A ref may be held once per queue, so a list; owners come from the page's items.
        stub_requests.queue(json_data={"page": {"page": 0, "size": 1, "total": 1}, "items": [ITEM_JSON]})

        result = client.get_items_by_ref(organisation_id=2, ref_type="file_id", ref_value="file_550e8400")

        call = stub_requests.calls[0]
        assert call.method == "GET"
        assert call.url == "http://cp.test/api/work_queues/_/items"
        assert call.kwargs["params"] == {"o": "2", "ref_type": "file_id", "ref_value": "file_550e8400", "s": "1000"}
        assert [item.id for item in result] == [4711]

    def test_queue_scope_uses_queue_id_path(self, client, stub_requests):
        stub_requests.queue(json_data={"page": {"page": 0, "size": 1, "total": 1}, "items": [ITEM_JSON]})

        result = client.get_items_by_ref(organisation_id=2, queue_id=12, ref_type="file_id", ref_value="file_550e8400")

        assert stub_requests.calls[0].url == "http://cp.test/api/work_queues/12/items"
        assert len(result) == 1

    def test_empty_items_means_no_owner(self, client, stub_requests):
        stub_requests.queue(json_data={"page": {"page": 0, "size": 0, "total": 0}, "items": []})

        result = client.get_items_by_ref(organisation_id=2, ref_type="content_hash", ref_value="missing")

        assert result == []

    def test_404_propagates(self, client, stub_requests):
        # A miss is 200 + empty items (above); a genuine 404 is still a real error and propagates.
        stub_requests.queue(json_data={"message": "not found"}, status_code=404)

        with pytest.raises(requests.HTTPError):
            client.get_items_by_ref(organisation_id=2, ref_type="file_id", ref_value="x")

    def test_propagates_other_errors(self, client, stub_requests):
        stub_requests.queue(json_data={"message": "boom"}, status_code=500)

        with pytest.raises(requests.HTTPError):
            client.get_items_by_ref(organisation_id=2, ref_type="file_id", ref_value="x")

    def test_old_singular_get_item_by_ref_is_removed(self, client):
        # Clean rename, no alias — an alias would perpetuate the wrong "single owner" model.
        assert not hasattr(client, "get_item_by_ref")


class TestDeleteItem:
    def test_delete_item_hits_item_endpoint_and_returns_none(self, client, stub_requests):
        stub_requests.queue(json_data={"success": True})

        result = client.delete_item(queue_id=12, item_id=4711, organisation_id=2)

        call = stub_requests.calls[0]
        assert call.method == "DELETE"
        assert call.url == "http://cp.test/api/work_queues/12/items/4711"
        assert call.kwargs["params"] == {"o": "2"}
        assert result is None

    def test_delete_item_propagates_404(self, client, stub_requests):
        stub_requests.queue(json_data={"message": "not found"}, status_code=404)

        with pytest.raises(requests.HTTPError):
            client.delete_item(queue_id=12, item_id=9999, organisation_id=2)


class TestListQueues:
    def test_list_hits_work_queues_endpoint_paged(self, client, stub_requests):
        stub_requests.queue(json_data={"page": {"page": 0, "size": 1, "total": 1}, "items": [QUEUE_JSON]})

        result = client.list(organisation_id=2)

        call = stub_requests.calls[0]
        assert call.method == "GET"
        assert call.url == "http://cp.test/api/work_queues"
        assert call.kwargs["params"] == {"o": "2", "p": "0", "s": "50"}
        assert result.page.total == 1
        assert [q.id for q in result.items] == [8]
        assert result.items[0].name == "Guideline Ingestion"

    def test_list_forwards_pagination(self, client, stub_requests):
        stub_requests.queue(json_data={"page": {"page": 2, "size": 10, "total": 0}, "items": []})

        client.list(organisation_id=9, page=2, size=10)

        assert stub_requests.calls[0].kwargs["params"] == {"o": "9", "p": "2", "s": "10"}
