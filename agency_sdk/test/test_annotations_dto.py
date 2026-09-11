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
    AnnotationBatchMember,
    AnnotationBatchMembersPagedResult,
    AnnotationBatchResponse,
    AnnotationJob,
    AnnotationJobsPagedResult,
    AnnotationJobSummary,
    AnnotationSpec,
    AnnotationSpecsPagedResult,
    AnnotationWorkflow,
    AnnotationWorkflowsPagedResult,
    BatchStatus,
    BindWorkflowResult,
    CreateBatchResult,
    CreateSpecResult,
    JobTransitionEntry,
    JobTransitionsPagedResult,
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


#: A batch as the control plane returns it since the job-counter split (gts-comand eda4f9ca):
#: `completed_jobs` is gone, replaced by resolved/accepted/rejected. Transcribed from a live
#: `GET /api/annotations/{id}?o=2`.
SPLIT_COUNTER_BATCH_JSON = {
    "id": "7324f779-3e61-418e-be3b-2a3faf296a27",
    "organisation_id": 2,
    "name": "DBQ: Knee and Lower Leg — source_text verification",
    "description": "Rebuilt from archived run xval17 with the current producer.",
    "instructions": None,
    "batch_type": "graph",
    "graph_uri": "2/annotations/7324f779-3e61-418e-be3b-2a3faf296a27/graph.json",
    "graph_run_id": "ac4-source-text-probe",
    "target_class": "rule",
    "context_hops": 1,
    "total_jobs": 14,
    "resolved_jobs": 3,
    "accepted_jobs": 2,
    "rejected_jobs": 1,
    "status": 1,
    "confidentiality_level": "INTERNAL",
    "audit_data": {"created_on": "2026-08-21 10:00:00Z", "created_by": "901"},
}

WORKFLOW_JSON = {
    "id": "sys-wf-graph-2",
    "code": "graph_two_step",
    "name": "Graph two-step review",
    "description": "One person annotates, a different person approves.",
    "target_batch_type": "graph",
    "is_system": True,
    "status": "active",
    "current_published_version_id": "sys-wfv-graph-2",
    "draft_version_id": None,
}


def test_batch_parses_the_split_job_counters():
    batch = AnnotationBatch(**SPLIT_COUNTER_BATCH_JSON)

    assert batch.total_jobs == 14
    assert batch.resolved_jobs == 3
    assert batch.accepted_jobs == 2
    assert batch.rejected_jobs == 1
    # The pre-split counter simply is not sent any more; it must not be required.
    assert batch.completed_jobs is None


def test_batch_still_parses_a_pre_split_server_payload():
    # One model has to cover both server generations: older control planes send
    # completed_jobs and none of the three new counters.
    batch = AnnotationBatch(**ACTIVE_BATCH_JSON)

    assert batch.completed_jobs == 0
    assert batch.resolved_jobs is None
    assert batch.accepted_jobs is None
    assert batch.rejected_jobs is None


def test_batch_response_parses_the_live_single_read():
    response = AnnotationBatchResponse(**{**SPLIT_COUNTER_BATCH_JSON, "viewer_role": "admin"})

    assert response.viewer_role == "admin"
    assert response.accepted_jobs == 2
    assert response.status == BatchStatus.ACTIVE


def test_annotation_workflow_deserialises():
    workflow = AnnotationWorkflow(**WORKFLOW_JSON)

    assert workflow.id == "sys-wf-graph-2"
    assert workflow.target_batch_type == "graph"
    assert workflow.is_system is True
    assert workflow.status == "active"
    assert workflow.current_published_version_id == "sys-wfv-graph-2"
    assert workflow.draft_version_id is None


def test_annotation_workflows_paged_result_wraps_page_and_items():
    result = AnnotationWorkflowsPagedResult(**{"page": {"page": 0, "size": 50, "total": 1}, "items": [WORKFLOW_JSON]})

    assert result.page.total == 1
    assert [w.code for w in result.items] == ["graph_two_step"]


def test_bind_workflow_result_carries_the_bound_version_and_regoverned_count():
    result = BindWorkflowResult(
        success=True, message="0 job(s) re-governed", workflow_version_id="sys-wfv-graph-2", jobs_regoverned=0
    )

    assert result.workflow_version_id == "sys-wfv-graph-2"
    assert result.jobs_regoverned == 0


# ---------------------------------------------------------------------------
# Job reads (issue #16). JSON transcribed from the gts-comand structs at
# `90f95ad8`: `AnnotationJob` / `AnnotationJobSummary`
# (`crates/comand/src/model/annotation.rs`) and `JobTransitionEntry`
# (`model/annotation_workflow.rs`).
# ---------------------------------------------------------------------------

#: A job as the LIST read returns it: identity and pipeline position only. The
#: server excludes the heavy payloads from this shape on purpose.
JOB_SUMMARY_JSON = {
    "id": "job-4c1a0f88-2b31-4e77-9a56-3d0e1f2a7b90",
    "batch_id": "7324f779-3e61-418e-be3b-2a3faf296a27",
    "organisation_id": 2,
    "job_type": "rule_validation",
    "vertex_bid": "v-rule-1",
    "vertex_name": "Knee MRI indications",
    "display": "Knee MRI indications",
    "state_code": "completed",
    "workflow_version_id": "sys-wfv-graph-2",
    "audit_data": {
        "created_on": "2026-08-30 11:00:00Z",
        "created_by": "901",
        "modified_on": "2026-08-31 08:42:10Z",
        "modified_by": "907",
    },
}

#: The annotator's edits. Deliberately irregular: comand never parses this column
#: (whole-value replacement) and its shape belongs to whichever front-end wrote it.
ANNOTATION_DATA_BLOB = {
    "display": "Knee MRI — indications",
    "english_description": "Order MRI only after six weeks of conservative care.",
    "page_references": "3, 5-7, 12",
    "variables": [
        {"key": "v0", "code": "GTS:12345", "codeDisplay": "Knee pain", "confirmed": True, "source": "search"},
        {"key": "new-1", "added": True, "code": "", "confirmed": True, "note": "no suitable concept"},
    ],
    "notes": "Reviewed against the 2026 revision.",
    "unexpected_future_field": {"nested": [1, 2, {"deep": None}]},
}

#: The same job as the SINGLE read returns it: everything above plus the payloads.
JOB_JSON = {
    **JOB_SUMMARY_JSON,
    "vertex_data": {"bid": "v-rule-1", "class": "rule", "metadata": {"display": "Knee MRI indications"}},
    "connected_vertices": [{"bid": "v-doc-1", "class": "document"}],
    "connected_edges": [{"from": "v-rule-1", "to": "v-doc-1", "label": "sourced_from"}],
    "delta": None,
    "data": None,
    "checklist_state": {"text_matches_source": True, "page_reference_correct": False},
    "annotation_data": ANNOTATION_DATA_BLOB,
    "revision": 4,
}

#: The accepting transition. `note` is PHI-capable free text and is never logged.
TRANSITION_JSON = {
    "id": 8814,
    "job_id": JOB_SUMMARY_JSON["id"],
    "batch_id": JOB_SUMMARY_JSON["batch_id"],
    "organisation_id": 2,
    "workflow_version_id": "sys-wfv-graph-1",
    "from_state": "in_review",
    "to_state": "completed",
    "transition_code": "approve",
    "actor_type": "user",
    "actor_user_id": 907,
    "acting_as_role": "approver",
    "note": None,
    "prior_actor_conflict": False,
    "occurred_on": "2026-08-31T08:42:10Z",
}


def test_annotation_job_summary_carries_identity_and_pipeline_position():
    summary = AnnotationJobSummary(**JOB_SUMMARY_JSON)

    assert summary.id == JOB_SUMMARY_JSON["id"]
    assert summary.batch_id == JOB_SUMMARY_JSON["batch_id"]
    assert summary.organisation_id == 2
    assert summary.job_type == "rule_validation"
    assert summary.vertex_bid == "v-rule-1"
    assert summary.vertex_name == "Knee MRI indications"
    assert summary.display == "Knee MRI indications"
    assert summary.state_code == "completed"
    assert summary.workflow_version_id == "sys-wfv-graph-2"
    # The Publisher's drift fence reads modified_on off this, so it must survive.
    assert summary.audit_data["modified_on"] == "2026-08-31 08:42:10Z"


def test_annotation_job_summary_tolerates_a_dataset_job_with_no_vertex():
    summary = AnnotationJobSummary(
        id="j",
        batch_id="b",
        organisation_id=2,
        job_type="categorization",
        vertex_bid=None,
        vertex_name=None,
        display=None,
        state_code="pending",
        workflow_version_id="wfv",
        audit_data=None,
    )

    assert (summary.vertex_bid, summary.vertex_name, summary.display) == (None, None, None)
    assert summary.audit_data is None


def test_annotation_job_parses_every_field_of_the_full_row():
    job = AnnotationJob(**JOB_JSON)

    assert job.id == JOB_JSON["id"]
    assert job.job_type == "rule_validation"
    assert job.vertex_bid == "v-rule-1"
    assert job.vertex_data["metadata"]["display"] == "Knee MRI indications"
    assert job.connected_vertices == JOB_JSON["connected_vertices"]
    assert job.connected_edges == JOB_JSON["connected_edges"]
    assert job.delta is None
    assert job.data is None
    assert job.checklist_state == {"text_matches_source": True, "page_reference_correct": False}
    assert job.state_code == "completed"
    assert job.workflow_version_id == "sys-wfv-graph-2"
    assert job.revision == 4
    assert job.audit_data == JOB_SUMMARY_JSON["audit_data"]


def test_annotation_job_is_not_a_subclass_of_the_summary():
    # Two distinct server structs that happen to overlap. Modelling the full row as
    # an extension of the list row would claim a substitutability the API does not
    # promise — and would let a summary flow into code that needs the payloads.
    assert not issubclass(AnnotationJob, AnnotationJobSummary)


def test_annotation_job_keeps_annotation_data_opaque():
    job = AnnotationJob(**JOB_JSON)

    # Byte-for-byte the blob the front-end wrote: not validated, not reshaped, not
    # key-filtered. Its structure belongs to whichever app saved it.
    assert job.annotation_data == ANNOTATION_DATA_BLOB
    assert job.annotation_data["unexpected_future_field"] == {"nested": [1, 2, {"deep": None}]}
    assert job.annotation_data["variables"][1]["key"] == "new-1"


def test_annotation_job_accepts_a_foreign_annotation_data_shape():
    # comand-web's JobVerificationPage writes a completely different structure into
    # the same column. The SDK must carry it through untouched so the caller can
    # detect the contamination itself.
    foreign = {"rule_text": "…", "confirmed_entities": [], "no_variables_key": True}
    job = AnnotationJob(**{**JOB_JSON, "annotation_data": foreign, "checklist_state": ["not", "a", "dict"]})

    assert job.annotation_data == foreign
    assert job.checklist_state == ["not", "a", "dict"]


def test_annotation_job_state_code_is_a_plain_string():
    # Declared String server-side, and its values are declared by the governing
    # workflow VERSION — workflow data, not an SDK-side constant. No enum.
    job = AnnotationJob(**{**JOB_JSON, "state_code": "some_future_state"})

    assert job.state_code == "some_future_state"
    assert isinstance(job.state_code, str)


def test_annotation_jobs_paged_result_wraps_page_and_items():
    result = AnnotationJobsPagedResult(**{"page": {"page": 0, "size": 50, "total": 1}, "items": [JOB_SUMMARY_JSON]})

    assert result.page.total == 1
    assert [j.state_code for j in result.items] == ["completed"]


def test_job_transition_entry_parses_the_ledger_row():
    entry = JobTransitionEntry(**TRANSITION_JSON)

    assert entry.id == 8814
    assert entry.job_id == JOB_SUMMARY_JSON["id"]
    assert entry.batch_id == JOB_SUMMARY_JSON["batch_id"]
    assert entry.organisation_id == 2
    assert entry.from_state == "in_review"
    assert entry.to_state == "completed"
    assert entry.transition_code == "approve"
    assert entry.actor_type == "user"
    assert entry.actor_user_id == 907
    assert entry.acting_as_role == "approver"
    assert entry.prior_actor_conflict is False
    assert entry.occurred_on == "2026-08-31T08:42:10Z"


def test_job_transition_entry_stamps_the_version_in_force_not_the_job_s_current_one():
    # Bindings move. Only the stamped copy makes a past transition attributable to
    # the policy that permitted it, so it must not be conflated with the job's.
    entry = JobTransitionEntry(**TRANSITION_JSON)
    job = AnnotationJob(**JOB_JSON)

    assert entry.workflow_version_id == "sys-wfv-graph-1"
    assert job.workflow_version_id == "sys-wfv-graph-2"


def test_job_transition_entry_optional_fields_default_to_none():
    entry = JobTransitionEntry(
        id=1,
        job_id="j",
        batch_id="b",
        organisation_id=2,
        workflow_version_id="wfv",
        to_state="pending",
        transition_code="create",
        actor_type="system",
        acting_as_role="system",
        prior_actor_conflict=False,
        occurred_on="2026-08-30T11:00:00Z",
    )

    assert entry.from_state is None
    assert entry.actor_user_id is None
    assert entry.note is None


def test_job_transition_entry_carries_the_conflict_flag_and_its_note():
    entry = JobTransitionEntry(
        **{**TRANSITION_JSON, "prior_actor_conflict": True, "note": "same reviewer as the annotator"}
    )

    assert entry.prior_actor_conflict is True
    assert entry.note == "same reviewer as the annotator"


def test_job_transition_entry_note_docstring_warns_it_is_phi_capable():
    # The warning is the point of the field: comand excludes it from its own access
    # log, and a caller reading the DTO must be told before they log it.
    description = JobTransitionEntry.model_fields["note"].description or ""

    assert "PHI" in description
    assert "log" in description.lower()


def test_job_transitions_paged_result_wraps_page_and_items():
    result = JobTransitionsPagedResult(**{"page": {"page": 0, "size": 50, "total": 1}, "items": [TRANSITION_JSON]})

    assert result.page.total == 1
    assert [e.transition_code for e in result.items] == ["approve"]


#: A member row as the server sends it. Note the audit fields are FLAT here, unlike
#: the batch and job models which nest them in ``audit_data`` — mirroring the server
#: rather than imposing consistency it does not have.
MEMBER_JSON = {
    "batch_id": "7324f779-3e61-418e-be3b-2a3faf296a27",
    "user_id": 907,
    "role": "admin",
    "eff_from": "2026-08-27T09:00:00Z",
    "eff_to": None,
    "created_on": "2026-08-27T09:00:00Z",
    "created_by": "901",
    "modified_on": None,
    "modified_by": None,
    "given_name": "Dana",
    "family_name": "Okonkwo",
    "known_as": "Dr Okonkwo",
    "email": "dana.okonkwo@example.org",
}


def test_annotation_batch_member_parses_the_row_with_the_servers_field_names():
    member = AnnotationBatchMember(**MEMBER_JSON)

    assert member.batch_id == MEMBER_JSON["batch_id"]
    assert member.user_id == 907
    assert member.role == "admin"
    # eff_from / eff_to, NOT effective_from / effective_to. The SDK mirrors the wire.
    assert member.eff_from == "2026-08-27T09:00:00Z"
    assert member.eff_to is None
    assert member.created_by == "901"


def test_an_open_membership_has_no_eff_to():
    member = AnnotationBatchMember(**MEMBER_JSON)

    assert member.eff_to is None  # still a member
    closed = AnnotationBatchMember(**{**MEMBER_JSON, "eff_to": "2026-09-01T00:00:00Z"})
    assert closed.eff_to == "2026-09-01T00:00:00Z"


def test_eff_from_is_required_but_the_display_fields_are_not():
    # The display fields are only populated on the joined read, so a row without
    # them is normal rather than malformed.
    bare = AnnotationBatchMember(
        batch_id="b",
        user_id=1,
        role="member",
        eff_from="2026-08-27T09:00:00Z",
        created_on="2026-08-27T09:00:00Z",
        created_by="901",
    )

    assert (bare.given_name, bare.family_name, bare.known_as, bare.email) == (None, None, None, None)
    assert bare.eff_to is None and bare.modified_on is None


def test_member_role_is_the_access_level_not_a_workflow_role():
    # annotation_batch_member.role is member/admin — batch ACCESS. The workflow
    # roles a transition stamps (annotator/reviewer/approver) are derived from
    # permission bits at action time and are not recorded here. A caller wanting
    # "when was this person an approver" must read the ledger, not this.
    assert AnnotationBatchMember(**MEMBER_JSON).role in {"member", "admin"}


def test_batch_members_paged_result_wraps_page_and_items():
    result = AnnotationBatchMembersPagedResult(**{"page": {"page": 0, "size": 50, "total": 1}, "items": [MEMBER_JSON]})

    assert result.page.total == 1
    assert [m.user_id for m in result.items] == [907]
    assert result.rejected == []
