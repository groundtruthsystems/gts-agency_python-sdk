"""DTO tests for the annotations delegate (graph batches → annotator jobs).

JSON transcribed from the gts-comand models (`crates/comand/src/model/annotation.rs`):
the server serialises every field, so nulls arrive explicitly rather than being
omitted, and `AnnotationBatchResponse` flattens the batch alongside `viewer_role`.
"""

from agency_sdk.delegates.annotations_dto import (
    BATCH_TYPE_GRAPH,
    DEFAULT_JOB_TYPE,
    DEFAULT_TARGET_CLASS,
    AnnotationBatch,
    AnnotationBatchesPagedResult,
    AnnotationBatchResponse,
    AnnotationSpec,
    AnnotationSpecsPagedResult,
    BatchStatus,
    CreateBatchResult,
    CreateSpecResult,
    PushGraphResult,
    SpecStatus,
)

#: A batch straight after `create` — DRAFT, no jobs, graph subtype fields still null.
DRAFT_BATCH_JSON = {
    "id": "7f1d9c62-1f2a-4a51-9a1e-2d0c3f5b8e40",
    "organisation_id": 2,
    "name": "MTUS Knee 2026",
    "description": None,
    "instructions": None,
    "batch_type": "graph",
    "graph_uri": None,
    "graph_run_id": None,
    "target_class": None,
    "context_hops": None,
    "total_jobs": 0,
    "completed_jobs": 0,
    "status": 0,
    "confidentiality_level": "INTERNAL",
    "audit_data": {
        "created_on": "2026-08-03 09:15:00Z",
        "created_by": "901",
        "modified_on": "2026-08-03 09:15:00Z",
        "modified_by": "901",
    },
}

#: The same batch after `upload` — ACTIVE, jobs materialised, subtype fields hydrated.
ACTIVE_BATCH_JSON = {
    **DRAFT_BATCH_JSON,
    "description": "MTUS knee guideline rules, 2026 revision",
    "instructions": "Confirm each rule against the source PDF page.",
    "graph_uri": "2/annotations/7f1d9c62-1f2a-4a51-9a1e-2d0c3f5b8e40/graph.json",
    "graph_run_id": "run-2026-08-03-a",
    "target_class": "rule",
    "context_hops": 1,
    "total_jobs": 325,
    "status": 1,
}

SPEC_JSON = {
    "id": "3b0e4d11-77c8-4a0b-9f3d-19c2b7d6a015",
    "organisation_id": 2,
    "code": "rule_validation",
    "name": "Rule validation",
    "instructions": "Check the rule text, its evidence level, and its page reference.",
    "checklist": [
        {"id": "text_matches_source", "label": "Rule text matches the source"},
        {"id": "page_reference_correct", "label": "Page reference is correct"},
    ],
    "status": 1,
    "audit_data": {"created_on": "2026-08-03 09:00:00Z", "created_by": "901"},
}


def test_annotation_batch_deserialises_a_draft_batch():
    batch = AnnotationBatch(**DRAFT_BATCH_JSON)

    assert batch.id == "7f1d9c62-1f2a-4a51-9a1e-2d0c3f5b8e40"
    assert batch.organisation_id == 2
    assert batch.name == "MTUS Knee 2026"
    assert batch.batch_type == BATCH_TYPE_GRAPH
    assert batch.status == BatchStatus.DRAFT
    assert batch.total_jobs == 0
    assert batch.completed_jobs == 0
    assert batch.confidentiality_level == "INTERNAL"
    assert batch.audit_data == DRAFT_BATCH_JSON["audit_data"]


def test_annotation_batch_tolerates_explicit_nulls_and_missing_optionals():
    from_nulls = AnnotationBatch(**DRAFT_BATCH_JSON)
    from_omitted = AnnotationBatch(
        id="x",
        organisation_id=2,
        name="n",
        batch_type="graph",
        total_jobs=0,
        completed_jobs=0,
        status=0,
        confidentiality_level="INTERNAL",
    )

    for batch in (from_nulls, from_omitted):
        assert batch.description is None
        assert batch.instructions is None
        assert batch.graph_uri is None
        assert batch.graph_run_id is None
        assert batch.target_class is None
        assert batch.context_hops is None
    assert from_omitted.audit_data is None


def test_annotation_batch_carries_graph_subtype_fields_once_uploaded():
    batch = AnnotationBatch(**ACTIVE_BATCH_JSON)

    assert batch.status == BatchStatus.ACTIVE
    assert batch.total_jobs == 325
    assert batch.graph_uri == "2/annotations/7f1d9c62-1f2a-4a51-9a1e-2d0c3f5b8e40/graph.json"
    assert batch.graph_run_id == "run-2026-08-03-a"
    assert batch.target_class == DEFAULT_TARGET_CLASS
    assert batch.context_hops == 1


def test_annotation_batch_response_flattens_the_batch_beside_viewer_role():
    response = AnnotationBatchResponse(**{**ACTIVE_BATCH_JSON, "viewer_role": "admin"})

    assert response.viewer_role == "admin"
    assert response.id == ACTIVE_BATCH_JSON["id"]
    assert response.total_jobs == 325
    assert response.status == BatchStatus.ACTIVE


def test_annotation_batch_response_viewer_role_defaults_to_none():
    response = AnnotationBatchResponse(**ACTIVE_BATCH_JSON)

    assert response.viewer_role is None


def test_annotation_batches_paged_result_wraps_page_and_items():
    result = AnnotationBatchesPagedResult(
        **{"page": {"page": 0, "size": 10, "total": 2}, "items": [DRAFT_BATCH_JSON, ACTIVE_BATCH_JSON]}
    )

    assert result.page.total == 2
    assert [b.status for b in result.items] == [BatchStatus.DRAFT, BatchStatus.ACTIVE]


def test_create_batch_result_carries_the_envelope_and_the_lifted_id():
    result = CreateBatchResult(success=True, message="Batch created: 7f1d9c62", id="7f1d9c62")

    assert result.success is True
    assert result.message == "Batch created: 7f1d9c62"
    assert result.id == "7f1d9c62"


def test_create_spec_result_is_its_own_type_with_the_same_envelope():
    result = CreateSpecResult(success=True, message="Specification created: 3b0e4d11", id="3b0e4d11")

    assert result.id == "3b0e4d11"
    assert not isinstance(result, CreateBatchResult)


def test_push_graph_result_reports_the_read_back_job_count():
    batch = AnnotationBatchResponse(**ACTIVE_BATCH_JSON)
    result = PushGraphResult(batch_id=batch.id, total_jobs=batch.total_jobs, status=batch.status, batch=batch)

    assert result.batch_id == ACTIVE_BATCH_JSON["id"]
    assert result.total_jobs == 325
    assert result.status == BatchStatus.ACTIVE
    assert result.batch.graph_run_id == "run-2026-08-03-a"


def test_annotation_spec_deserialises_with_its_checklist():
    spec = AnnotationSpec(**SPEC_JSON)

    assert spec.code == DEFAULT_JOB_TYPE
    assert spec.name == "Rule validation"
    assert spec.status == SpecStatus.ACTIVE
    assert [item["id"] for item in spec.checklist] == ["text_matches_source", "page_reference_correct"]


def test_annotation_spec_optional_fields_default_to_none():
    spec = AnnotationSpec(id="x", organisation_id=2, code="c", name="n", checklist=[], status=0)

    assert spec.instructions is None
    assert spec.audit_data is None
    assert spec.status == SpecStatus.DRAFT


def test_annotation_specs_paged_result_wraps_page_and_items():
    result = AnnotationSpecsPagedResult(**{"page": {"page": 0, "size": 10, "total": 1}, "items": [SPEC_JSON]})

    assert result.page.total == 1
    assert [s.code for s in result.items] == ["rule_validation"]


def test_status_enums_match_the_server_constants():
    assert (BatchStatus.DRAFT, BatchStatus.ACTIVE, BatchStatus.COMPLETED, BatchStatus.ARCHIVED) == (0, 1, 2, 3)
    assert (SpecStatus.DRAFT, SpecStatus.ACTIVE, SpecStatus.ARCHIVED) == (0, 1, 2)


def test_module_constants_document_the_server_defaults():
    assert (BATCH_TYPE_GRAPH, DEFAULT_JOB_TYPE, DEFAULT_TARGET_CLASS) == ("graph", "rule_validation", "rule")
