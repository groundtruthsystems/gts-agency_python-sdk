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
