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
    """The push legs other than workflow resolution, which TestPushGraphBindsAWorkflow owns.

    These pass ``workflow_id`` so the workflow lookup is skipped and each test stays
    about one thing.
    """

    def _queue_happy_path(self, stub_requests):
        stub_requests.queue(json_data=CREATED_ENVELOPE)  # create
        stub_requests.queue(json_data=BOUND_ENVELOPE)  # bind
        stub_requests.queue(json_data=None)  # upload (null body)
        stub_requests.queue(json_data=ACTIVE_BATCH_JSON)  # read-back

    def test_reports_the_read_back_batch(self, client, stub_requests):
        self._queue_happy_path(stub_requests)

        result = client.push_graph(organisation_id=2, name="MTUS Knee 2026", graph=GRAPH, workflow_id="w-1")

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
            workflow_id="w-1",
        )

        create, _bind, upload, _read_back = stub_requests.calls
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

        result = client.push_graph(organisation_id=2, name="MTUS Knee 2026", file_path=graph_file, workflow_id="w-1")

        assert json.loads(stub_requests.calls[2].kwargs["files"]["file"][1]) == GRAPH
        assert result.total_jobs == 325

    def test_validates_the_graph_source_before_creating_a_batch(self, client, stub_requests):
        with pytest.raises(ValueError, match="exactly one"):
            client.push_graph(organisation_id=2, name="MTUS Knee 2026")

        assert stub_requests.calls == []

    def test_a_failed_upload_propagates_and_leaves_the_draft_batch(self, client, stub_requests):
        stub_requests.queue(json_data=CREATED_ENVELOPE)
        stub_requests.queue(json_data=BOUND_ENVELOPE)
        stub_requests.queue(json_data={"error": {"message": "No vertices of class 'rule' found"}}, status_code=400)

        with pytest.raises(requests.HTTPError):
            client.push_graph(organisation_id=2, name="MTUS Knee 2026", graph={"vertices": []}, workflow_id="w-1")

        # The batch was created and bound before the upload failed: three calls, no
        # read-back, and the DRAFT batch survives server-side (documented residue,
        # recoverable via list_batches).
        assert len(stub_requests.calls) == 3


WORKFLOWS_PAGE = {
    "page": {"page": 0, "size": 50, "total": 3},
    "items": [
        {
            "id": "sys-wf-dataset-2",
            "code": "dataset_single_step",
            "name": "Dataset single step",
            "description": None,
            "target_batch_type": "dataset",
            "is_system": True,
            "status": "active",
            "current_published_version_id": "sys-wfv-dataset-2",
            "draft_version_id": None,
        },
        {
            "id": "org-wf-graph-draft",
            "code": "graph_draft_only",
            "name": "Graph, never published",
            "description": None,
            "target_batch_type": "graph",
            "is_system": False,
            "status": "active",
            "current_published_version_id": None,
            "draft_version_id": "draft-1",
        },
        {
            "id": "sys-wf-graph-2",
            "code": "graph_two_step",
            "name": "Graph two-step review",
            "description": None,
            "target_batch_type": "graph",
            "is_system": True,
            "status": "active",
            "current_published_version_id": "sys-wfv-graph-2",
            "draft_version_id": None,
        },
    ],
}

BOUND_ENVELOPE = {
    "success": True,
    "message": "0 job(s) re-governed",
    "data": {"jobs_regoverned": 0, "workflow_version_id": "sys-wfv-graph-2"},
}


class TestWorkflows:
    def test_list_workflows_hits_the_workflows_root(self, client, stub_requests):
        stub_requests.queue(json_data=WORKFLOWS_PAGE)

        result = client.list_workflows(organisation_id=2)

        call = stub_requests.calls[0]
        assert call.method == "GET"
        assert call.url == "http://cp.test/api/annotation-workflows"
        assert call.kwargs["params"] == {"o": "2", "p": "0", "s": "50"}
        assert [w.id for w in result.items] == ["sys-wf-dataset-2", "org-wf-graph-draft", "sys-wf-graph-2"]

    def test_list_workflows_forwards_pagination(self, client, stub_requests):
        stub_requests.queue(json_data={"page": {"page": 1, "size": 5, "total": 0}, "items": []})

        client.list_workflows(organisation_id=9, page=1, size=5)

        assert stub_requests.calls[0].kwargs["params"] == {"o": "9", "p": "1", "s": "5"}

    def test_bind_workflow_posts_the_command_envelope(self, client, stub_requests):
        stub_requests.queue(json_data=BOUND_ENVELOPE)

        result = client.bind_workflow(organisation_id=2, batch_id="b-1", workflow_id="sys-wf-graph-2")

        call = stub_requests.calls[0]
        assert call.method == "POST"
        assert call.url == "http://cp.test/api/annotations/b-1/_command"
        assert call.kwargs["json"] == {
            "command": "bind_workflow",
            "organisation": 2,
            "payload": {"job_type": "*", "workflow_id": "sys-wf-graph-2"},
        }
        assert result.workflow_version_id == "sys-wfv-graph-2"
        assert result.jobs_regoverned == 0

    def test_bind_workflow_forwards_job_type_and_rebind_reason(self, client, stub_requests):
        stub_requests.queue(json_data=BOUND_ENVELOPE)

        client.bind_workflow(
            organisation_id=2,
            batch_id="b-1",
            workflow_id="sys-wf-graph-2",
            job_type="dbq_rules",
            rebind_reason="moving to the two-step review",
        )

        assert stub_requests.calls[0].kwargs["json"]["payload"] == {
            "job_type": "dbq_rules",
            "workflow_id": "sys-wf-graph-2",
            "rebind_reason": "moving to the two-step review",
        }


class TestPushGraphBindsAWorkflow:
    """Issue #14: a batch cannot hold jobs until a workflow is bound to it.

    The server's insert trigger refuses every job otherwise, surfacing as an opaque
    500 from the upload, so push_graph has to bind between create and upload.
    """

    def _queue_full_push(self, stub_requests):
        stub_requests.queue(json_data=WORKFLOWS_PAGE)  # resolve
        stub_requests.queue(json_data=CREATED_ENVELOPE)  # create
        stub_requests.queue(json_data=BOUND_ENVELOPE)  # bind
        stub_requests.queue(json_data=None)  # upload (null body)
        stub_requests.queue(json_data=ACTIVE_BATCH_JSON)  # read back

    def test_resolves_creates_binds_uploads_then_reads_back_in_order(self, client, stub_requests):
        self._queue_full_push(stub_requests)

        result = client.push_graph(organisation_id=2, name="MTUS Knee 2026", graph=GRAPH)

        batch_id = CREATED_ENVELOPE["data"]["id"]
        assert [(c.method, c.url) for c in stub_requests.calls] == [
            ("GET", "http://cp.test/api/annotation-workflows"),
            ("POST", "http://cp.test/api/annotations/_command"),
            ("POST", f"http://cp.test/api/annotations/{batch_id}/_command"),
            ("POST", f"http://cp.test/api/annotations/{batch_id}/upload"),
            ("GET", f"http://cp.test/api/annotations/{batch_id}"),
        ]
        assert result.total_jobs == 325

    def test_binds_the_published_system_workflow_matching_the_batch_type(self, client, stub_requests):
        # The page also holds a dataset workflow and an unpublished graph one; neither
        # is bindable here — the dataset one governs the wrong batch type, and a
        # workflow with no published version cannot be bound at all.
        self._queue_full_push(stub_requests)

        client.push_graph(organisation_id=2, name="n", graph=GRAPH)

        assert stub_requests.calls[2].kwargs["json"]["payload"] == {
            "job_type": "*",
            "workflow_id": "sys-wf-graph-2",
        }

    def test_an_explicit_workflow_id_skips_the_lookup(self, client, stub_requests):
        stub_requests.queue(json_data=CREATED_ENVELOPE)
        stub_requests.queue(json_data=BOUND_ENVELOPE)
        stub_requests.queue(json_data=None)
        stub_requests.queue(json_data=ACTIVE_BATCH_JSON)

        client.push_graph(organisation_id=2, name="n", graph=GRAPH, workflow_id="org-wf-custom")

        assert stub_requests.calls[0].url == "http://cp.test/api/annotations/_command"  # no workflow list
        assert stub_requests.calls[1].kwargs["json"]["payload"]["workflow_id"] == "org-wf-custom"

    def test_forwards_a_rebind_reason(self, client, stub_requests):
        stub_requests.queue(json_data=CREATED_ENVELOPE)
        stub_requests.queue(json_data=BOUND_ENVELOPE)
        stub_requests.queue(json_data=None)
        stub_requests.queue(json_data=ACTIVE_BATCH_JSON)

        client.push_graph(
            organisation_id=2, name="n", graph=GRAPH, workflow_id="w-1", rebind_reason="switching review flow"
        )

        assert stub_requests.calls[1].kwargs["json"]["payload"]["rebind_reason"] == "switching review flow"

    def test_no_bindable_workflow_raises_before_creating_a_batch(self, client, stub_requests):
        # Only a dataset workflow on offer: pushing a graph batch cannot proceed, and
        # must not leave a batch behind while failing.
        stub_requests.queue(
            json_data={"page": {"page": 0, "size": 50, "total": 1}, "items": [WORKFLOWS_PAGE["items"][0]]}
        )

        with pytest.raises(ValueError, match="no bindable annotation workflow"):
            client.push_graph(organisation_id=2, name="n", graph=GRAPH)

        assert len(stub_requests.calls) == 1  # the lookup only; nothing was created

    def test_a_pre_workflow_server_404s_the_lookup_and_the_bind_is_skipped(self, client, stub_requests):
        # Control planes older than the workflow model have no such endpoint and need
        # no binding; the push must still work against them.
        stub_requests.queue(json_data={"error": {"message": "Not Found"}}, status_code=404)
        stub_requests.queue(json_data=CREATED_ENVELOPE)
        stub_requests.queue(json_data=None)
        stub_requests.queue(json_data=ACTIVE_BATCH_JSON)

        result = client.push_graph(organisation_id=2, name="n", graph=GRAPH)

        assert [c.method for c in stub_requests.calls] == ["GET", "POST", "POST", "GET"]
        assert "_command" not in stub_requests.calls[2].url  # straight to the upload
        assert result.total_jobs == 325

    def test_other_errors_from_the_lookup_propagate(self, client, stub_requests):
        stub_requests.queue(json_data={"error": {"message": "boom"}}, status_code=500)

        with pytest.raises(requests.HTTPError):
            client.push_graph(organisation_id=2, name="n", graph=GRAPH)

        assert len(stub_requests.calls) == 1

    def test_validates_the_graph_source_before_anything_else(self, client, stub_requests):
        with pytest.raises(ValueError, match="exactly one"):
            client.push_graph(organisation_id=2, name="n")

        assert stub_requests.calls == []
