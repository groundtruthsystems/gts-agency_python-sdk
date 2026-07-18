"""Offline protocol tests for AgencyWorkQueueClient.

Every test asserts the exact wire contract of the Track ① work-queue ingestion
surface (files-inbox-ingestion design §5/§6): `?o={org}` on every call, the
flat `{"command": ...}` envelope, and — the load-bearing behaviour — 409 as
control flow: `create_item` / `add_ref` catch the HTTPError and return a typed
result (`created=False` / `added=False` + the owner fields), never re-raise.
"""

import pytest
import requests

from agency_sdk.delegates.work_queue_client import AgencyWorkQueueClient
from agency_sdk.delegates.work_queue_dto import CreateItemResult, ItemCommandResponse, ItemResponse
from agency_sdk.test.test_work_queue_dto import CREATE_CONFLICT_JSON, ITEM_JSON

ADD_REF_CONFLICT_JSON = {"work_item_id": 4711, "status": "blocked"}

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
        stub_requests.queue(json_data=CREATE_CONFLICT_JSON, status_code=409)

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
        assert result.existing is not None
        assert result.existing.work_item_id == 4711
        assert result.existing.status == "doing"
        assert result.existing.published is True

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
        stub_requests.queue(json_data=ADD_REF_CONFLICT_JSON, status_code=409)

        result = client.add_ref(
            queue_id=12, item_id=4712, organisation_id=2, ref_type="content_hash", ref_value="deadbeef"
        )

        assert result.added is False
        assert result.owner_work_item_id == 4711
        assert result.owner_status == "blocked"

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


class TestGetItemByRef:
    def test_get_item_by_ref_is_org_scoped_not_queue_scoped(self, client, stub_requests):
        stub_requests.queue(json_data=ITEM_JSON)

        result = client.get_item_by_ref(organisation_id=2, ref_type="file_id", ref_value="file_550e8400")

        call = stub_requests.calls[0]
        assert call.method == "GET"
        assert call.url == "http://cp.test/api/work_queues/items/_by_ref"
        assert call.kwargs["params"] == {"o": "2", "ref_type": "file_id", "ref_value": "file_550e8400"}
        assert result is not None
        assert result.id == 4711

    def test_get_item_by_ref_returns_none_on_404(self, client, stub_requests):
        stub_requests.queue(json_data={"message": "not found"}, status_code=404)

        result = client.get_item_by_ref(organisation_id=2, ref_type="content_hash", ref_value="missing")

        assert result is None

    def test_get_item_by_ref_propagates_other_errors(self, client, stub_requests):
        stub_requests.queue(json_data={"message": "boom"}, status_code=500)

        with pytest.raises(requests.HTTPError):
            client.get_item_by_ref(organisation_id=2, ref_type="file_id", ref_value="x")


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
