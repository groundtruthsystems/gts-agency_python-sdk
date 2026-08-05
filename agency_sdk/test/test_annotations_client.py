"""Offline protocol tests for AgencyAnnotationsClient.

Publishing is two calls — create a DRAFT batch, then upload the graph that
materialises the jobs — so these assert the exact wire shapes of both legs: the
``{command, organisation, payload}`` envelope with unset fields omitted, and the
multipart ``file`` field with only the query params the caller actually supplied.
"""

import json

import pytest
import requests

from agency_sdk.delegates.annotations_client import AgencyAnnotationsClient
from agency_sdk.delegates.annotations_dto import BatchStatus, SpecStatus
from agency_sdk.test.test_annotations_dto import ACTIVE_BATCH_JSON, DRAFT_BATCH_JSON, SPEC_JSON

GRAPH = {
    "run_id": "run-2026-08-03-a",
    "vertices": [
        {"bid": "v-rule-1", "class": "rule", "name": "Knee MRI indications"},
        {"bid": "v-rule-2", "class": "rule", "name": "Conservative care first"},
        {"bid": "v-doc-1", "class": "document", "name": "MTUS 2026"},
    ],
    "edges": [{"from": "v-rule-1", "to": "v-doc-1", "label": "sourced_from"}],
}

CREATED_ENVELOPE = {
    "success": True,
    "message": "Batch created: 7f1d9c62-1f2a-4a51-9a1e-2d0c3f5b8e40",
    "data": {"id": "7f1d9c62-1f2a-4a51-9a1e-2d0c3f5b8e40"},
}


@pytest.fixture
def client(fake_credentials):
    return AgencyAnnotationsClient(token_supplier=fake_credentials, base_url="http://cp.test/")


class TestCreateBatch:
    def test_posts_the_command_envelope_and_lifts_the_id(self, client, stub_requests):
        stub_requests.queue(json_data=CREATED_ENVELOPE)

        result = client.create_batch(organisation_id=2, name="MTUS Knee 2026")

        call = stub_requests.calls[0]
        assert call.method == "POST"
        assert call.url == "http://cp.test/api/annotations/_command"
        assert call.kwargs["json"] == {
            "command": "create",
            "organisation": 2,
            "payload": {"name": "MTUS Knee 2026", "batch_type": "graph"},
        }
        assert result.id == "7f1d9c62-1f2a-4a51-9a1e-2d0c3f5b8e40"
        assert result.success is True
        assert result.message == CREATED_ENVELOPE["message"]

    def test_omits_unset_payload_fields(self, client, stub_requests):
        stub_requests.queue(json_data=CREATED_ENVELOPE)

        client.create_batch(organisation_id=2, name="n")

        payload = stub_requests.calls[0].kwargs["json"]["payload"]
        assert set(payload) == {"name", "batch_type"}

    def test_sends_every_supplied_payload_field(self, client, stub_requests):
        stub_requests.queue(json_data=CREATED_ENVELOPE)

        client.create_batch(
            organisation_id=2,
            name="MTUS Knee 2026",
            description="2026 revision",
            instructions="Check each rule against the source PDF.",
            batch_type="dataset",
            confidentiality_level="RESTRICTED",
        )

        assert stub_requests.calls[0].kwargs["json"]["payload"] == {
            "name": "MTUS Knee 2026",
            "description": "2026 revision",
            "instructions": "Check each rule against the source PDF.",
            "batch_type": "dataset",
            "confidentiality_level": "RESTRICTED",
        }

    def test_raises_when_the_envelope_carries_no_id(self, client, stub_requests):
        stub_requests.queue(json_data={"success": True, "message": "Batch created", "data": None})

        with pytest.raises(ValueError, match="no batch id"):
            client.create_batch(organisation_id=2, name="n")

    def test_raises_when_data_is_absent_entirely(self, client, stub_requests):
        stub_requests.queue(json_data={"success": True, "message": "Batch created"})

        with pytest.raises(ValueError, match="no batch id"):
            client.create_batch(organisation_id=2, name="n")


class TestUploadGraph:
    def test_posts_the_graph_as_a_multipart_file_field(self, client, stub_requests):
        stub_requests.queue(json_data=None)

        assert client.upload_graph(organisation_id=2, batch_id="b-1", graph=GRAPH) is None

        call = stub_requests.calls[0]
        assert call.method == "POST"
        assert call.url == "http://cp.test/api/annotations/b-1/upload"
        assert call.kwargs["headers"] == {"Authorization": "Bearer test-token"}
        assert call.kwargs["timeout"] == 300
        filename, body, content_type = call.kwargs["files"]["file"]
        assert filename == "graph.json"
        assert content_type == "application/json"
        assert json.loads(body) == GRAPH

    def test_sends_only_the_query_params_supplied(self, client, stub_requests):
        stub_requests.queue(json_data=None)

        client.upload_graph(organisation_id=2, batch_id="b-1", graph=GRAPH)

        assert stub_requests.calls[0].kwargs["params"] == {"o": "2"}

    def test_forwards_job_generation_params_when_given(self, client, stub_requests):
        stub_requests.queue(json_data=None)

        client.upload_graph(
            organisation_id=2,
            batch_id="b-1",
            graph=GRAPH,
            job_type="rule_validation",
            target_class="rule",
            hops=2,
        )

        assert stub_requests.calls[0].kwargs["params"] == {
            "o": "2",
            "job_type": "rule_validation",
            "target_class": "rule",
            "hops": "2",
        }

    def test_a_file_path_and_a_dict_produce_the_same_body(self, client, stub_requests, tmp_path):
        graph_file = tmp_path / "sandbox_graph.json"
        graph_file.write_text(json.dumps(GRAPH))
        stub_requests.queue(json_data=None)
        stub_requests.queue(json_data=None)

        client.upload_graph(organisation_id=2, batch_id="b-1", graph=GRAPH)
        client.upload_graph(organisation_id=2, batch_id="b-1", file_path=graph_file)

        from_dict = stub_requests.calls[0].kwargs["files"]["file"]
        from_path = stub_requests.calls[1].kwargs["files"]["file"]
        assert from_dict[1] == from_path[1]
        assert from_path[0] == "sandbox_graph.json"

    def test_filename_can_be_overridden(self, client, stub_requests):
        stub_requests.queue(json_data=None)

        client.upload_graph(organisation_id=2, batch_id="b-1", graph=GRAPH, filename="mtus-knee.json")

        assert stub_requests.calls[0].kwargs["files"]["file"][0] == "mtus-knee.json"

    def test_rejects_both_graph_and_file_path_before_any_call(self, client, stub_requests, tmp_path):
        graph_file = tmp_path / "graph.json"
        graph_file.write_text("{}")

        with pytest.raises(ValueError, match="exactly one"):
            client.upload_graph(organisation_id=2, batch_id="b-1", graph=GRAPH, file_path=graph_file)

        assert stub_requests.calls == []

    def test_rejects_neither_graph_nor_file_path_before_any_call(self, client, stub_requests):
        with pytest.raises(ValueError, match="exactly one"):
            client.upload_graph(organisation_id=2, batch_id="b-1")

        assert stub_requests.calls == []

    def test_propagates_a_400_from_the_server(self, client, stub_requests):
        stub_requests.queue(
            json_data={"error": {"message": "No vertices of class 'rule' found in graph"}}, status_code=400
        )

        with pytest.raises(requests.HTTPError):
            client.upload_graph(organisation_id=2, batch_id="b-1", graph={"vertices": []})


class TestBatchReads:
    def test_get_batch_reads_the_flattened_response(self, client, stub_requests):
        stub_requests.queue(json_data={**ACTIVE_BATCH_JSON, "viewer_role": "admin"})

        batch = client.get_batch(organisation_id=2, batch_id=ACTIVE_BATCH_JSON["id"])

        call = stub_requests.calls[0]
        assert call.method == "GET"
        assert call.url == f"http://cp.test/api/annotations/{ACTIVE_BATCH_JSON['id']}"
        assert call.kwargs["params"] == {"o": "2"}
        assert batch.viewer_role == "admin"
        assert batch.status == BatchStatus.ACTIVE
        assert batch.total_jobs == 325
        assert batch.graph_run_id == "run-2026-08-03-a"

    def test_get_batch_of_a_draft_reports_no_jobs_yet(self, client, stub_requests):
        stub_requests.queue(json_data=DRAFT_BATCH_JSON)

        batch = client.get_batch(organisation_id=2, batch_id=DRAFT_BATCH_JSON["id"])

        assert batch.status == BatchStatus.DRAFT
        assert batch.total_jobs == 0
        assert batch.viewer_role is None

    def test_list_batches_pages_with_defaults(self, client, stub_requests):
        stub_requests.queue(json_data={"page": {"page": 0, "size": 50, "total": 1}, "items": [DRAFT_BATCH_JSON]})

        result = client.list_batches(organisation_id=2)

        call = stub_requests.calls[0]
        assert call.method == "GET"
        assert call.url == "http://cp.test/api/annotations"
        assert call.kwargs["params"] == {"o": "2", "p": "0", "s": "50"}
        assert [b.name for b in result.items] == ["MTUS Knee 2026"]

    def test_list_batches_forwards_pagination_and_filters(self, client, stub_requests):
        stub_requests.queue(json_data={"page": {"page": 2, "size": 5, "total": 0}, "items": []})

        client.list_batches(organisation_id=9, page=2, size=5, batch_type="graph", view="mine")

        assert stub_requests.calls[0].kwargs["params"] == {
            "o": "9",
            "p": "2",
            "s": "5",
            "batch_type": "graph",
            "view": "mine",
        }


class TestSpecs:
    def test_create_spec_posts_to_the_specs_root(self, client, stub_requests):
        stub_requests.queue(
            json_data={"success": True, "message": "Specification created: 3b0e", "data": {"id": "3b0e"}}
        )

        result = client.create_spec(
            organisation_id=2,
            code="rule_validation",
            name="Rule validation",
            checklist=[{"id": "text_matches_source", "label": "Rule text matches the source"}],
        )

        call = stub_requests.calls[0]
        assert call.method == "POST"
        assert call.url == "http://cp.test/api/annotation-specs/_command"
        assert call.kwargs["json"] == {
            "command": "create",
            "organisation": 2,
            "payload": {
                "code": "rule_validation",
                "name": "Rule validation",
                "checklist": [{"id": "text_matches_source", "label": "Rule text matches the source"}],
            },
        }
        assert result.id == "3b0e"

    def test_create_spec_sends_instructions_when_given(self, client, stub_requests):
        stub_requests.queue(json_data={"success": True, "message": "created", "data": {"id": "3b0e"}})

        client.create_spec(
            organisation_id=2, code="c", name="n", checklist=[], instructions="Check the page reference."
        )

        assert stub_requests.calls[0].kwargs["json"]["payload"]["instructions"] == "Check the page reference."

    def test_create_spec_raises_when_the_envelope_carries_no_id(self, client, stub_requests):
        stub_requests.queue(json_data={"success": True, "message": "created"})

        with pytest.raises(ValueError, match="no specification id"):
            client.create_spec(organisation_id=2, code="c", name="n", checklist=[])

    def test_get_spec_puts_the_CODE_in_the_path(self, client, stub_requests):
        stub_requests.queue(json_data=SPEC_JSON)

        spec = client.get_spec(organisation_id=2, code="rule_validation")

        call = stub_requests.calls[0]
        assert call.method == "GET"
        assert call.url == "http://cp.test/api/annotation-specs/rule_validation"
        assert call.kwargs["params"] == {"o": "2"}
        assert spec.id == SPEC_JSON["id"]
        assert spec.status == SpecStatus.ACTIVE

    def test_get_spec_propagates_a_404_for_an_unseeded_code(self, client, stub_requests):
        stub_requests.queue(json_data={"error": {"message": "Specification 'nope' not found"}}, status_code=404)

        with pytest.raises(requests.HTTPError):
            client.get_spec(organisation_id=2, code="nope")

    def test_list_specs_pages_over_the_specs_root(self, client, stub_requests):
        stub_requests.queue(json_data={"page": {"page": 0, "size": 50, "total": 1}, "items": [SPEC_JSON]})

        result = client.list_specs(organisation_id=2)

        call = stub_requests.calls[0]
        assert call.url == "http://cp.test/api/annotation-specs"
        assert call.kwargs["params"] == {"o": "2", "p": "0", "s": "50"}
        assert [s.code for s in result.items] == ["rule_validation"]

    def test_list_specs_forwards_pagination(self, client, stub_requests):
        stub_requests.queue(json_data={"page": {"page": 1, "size": 5, "total": 0}, "items": []})

        client.list_specs(organisation_id=9, page=1, size=5)

        assert stub_requests.calls[0].kwargs["params"] == {"o": "9", "p": "1", "s": "5"}


class TestPushGraph:
    def _queue_happy_path(self, stub_requests):
        stub_requests.queue(json_data=CREATED_ENVELOPE)  # create
        stub_requests.queue(json_data=None)  # upload (null body)
        stub_requests.queue(json_data=ACTIVE_BATCH_JSON)  # read-back

    def test_creates_uploads_and_reads_back_in_order(self, client, stub_requests):
        self._queue_happy_path(stub_requests)

        result = client.push_graph(organisation_id=2, name="MTUS Knee 2026", graph=GRAPH)

        create, upload, read_back = stub_requests.calls
        assert (create.method, create.url) == ("POST", "http://cp.test/api/annotations/_command")
        assert upload.url == f"http://cp.test/api/annotations/{CREATED_ENVELOPE['data']['id']}/upload"
        assert (read_back.method, read_back.url) == (
            "GET",
            f"http://cp.test/api/annotations/{CREATED_ENVELOPE['data']['id']}",
        )
        assert result.batch_id == CREATED_ENVELOPE["data"]["id"]
        assert result.total_jobs == 325
        assert result.status == BatchStatus.ACTIVE
        assert result.batch.graph_run_id == "run-2026-08-03-a"

    def test_threads_batch_and_upload_options_through(self, client, stub_requests):
        self._queue_happy_path(stub_requests)

        client.push_graph(
            organisation_id=2,
            name="MTUS Knee 2026",
            graph=GRAPH,
            description="2026 revision",
            instructions="Check each rule.",
            confidentiality_level="RESTRICTED",
            job_type="rule_validation",
            target_class="rule",
            hops=2,
        )

        create, upload, _ = stub_requests.calls
        assert create.kwargs["json"]["payload"] == {
            "name": "MTUS Knee 2026",
            "batch_type": "graph",
            "description": "2026 revision",
            "instructions": "Check each rule.",
            "confidentiality_level": "RESTRICTED",
        }
        assert upload.kwargs["params"] == {
            "o": "2",
            "job_type": "rule_validation",
            "target_class": "rule",
            "hops": "2",
        }

    def test_pushes_a_graph_file(self, client, stub_requests, tmp_path):
        graph_file = tmp_path / "sandbox_graph.json"
        graph_file.write_text(json.dumps(GRAPH))
        self._queue_happy_path(stub_requests)

        result = client.push_graph(organisation_id=2, name="MTUS Knee 2026", file_path=graph_file)

        assert json.loads(stub_requests.calls[1].kwargs["files"]["file"][1]) == GRAPH
        assert result.total_jobs == 325

    def test_validates_the_graph_source_before_creating_a_batch(self, client, stub_requests):
        with pytest.raises(ValueError, match="exactly one"):
            client.push_graph(organisation_id=2, name="MTUS Knee 2026")

        assert stub_requests.calls == []

    def test_a_failed_upload_propagates_and_leaves_the_draft_batch(self, client, stub_requests):
        stub_requests.queue(json_data=CREATED_ENVELOPE)
        stub_requests.queue(json_data={"error": {"message": "No vertices of class 'rule' found"}}, status_code=400)

        with pytest.raises(requests.HTTPError):
            client.push_graph(organisation_id=2, name="MTUS Knee 2026", graph={"vertices": []})

        # The batch was created before the upload failed: two calls, no read-back, and the
        # DRAFT batch survives server-side (documented residue, recoverable via list_batches).
        assert len(stub_requests.calls) == 2
