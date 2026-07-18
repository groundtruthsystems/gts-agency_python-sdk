"""DTO deserialisation tests for the work-queue delegate.

ItemResponse JSON samples are transcribed from the server DTO in
gts-agency-control/src/service/work_queue/work_queue_dto.rs (``ItemResponse``,
which omits Option fields entirely when null). The 409 conflict bodies follow
the frozen Track ① contract (files-inbox-ingestion design §2/§5.2): create → 409
carries ``{work_item_id, status, published}``; add_ref → 409 carries the owner
``{work_item_id, status}`` only.
"""

from agency_sdk.delegates.work_queue_dto import (
    AddRefResult,
    CreateItemResult,
    DependencyResponse,
    ExistingItemSummary,
    ItemCommandResponse,
    ItemResponse,
)

ITEM_JSON = {
    "id": 4711,
    "work_queue_id": 12,
    "title": "Ingest inbox/report.pdf",
    "description": "files-inbox ingestion card",
    "status": "backlog",
    "published": False,
    "priority": 0,
    "session_template_id": "guideline-extraction",
    "session_id": "sess-0001",
    "assigned_worker_id": "worker-7",
    "input_data": {"file_id": "file_550e8400", "path": "inbox/report.pdf"},
    "saved_state": {"checkpoint": 3},
    "result_data": {"notes": 17},
    "metadata": {"filename": "report.pdf", "size_bytes": 12345},
    "started_on": "2026-07-13T12:01:00Z",
    "created_on": "2026-07-13T12:00:00Z",
    "modified_on": "2026-07-13T12:02:00Z",
}

# The server serializes with skip_serializing_if = Option::is_none, so a fresh
# unpublished card arrives with every nullable field absent, not null.
MINIMAL_ITEM_JSON = {
    "id": 4712,
    "work_queue_id": 12,
    "title": "Ingest inbox/other.pdf",
    "status": "backlog",
    "published": False,
    "priority": 0,
    "created_on": "2026-07-13T12:00:00Z",
    "modified_on": "2026-07-13T12:00:00Z",
}

CREATE_CONFLICT_JSON = {"work_item_id": 4711, "status": "doing", "published": True}

DEPENDENCY_JSON = {
    "depends_on_item_id": 4700,
    "depends_on_title": "Classify folder",
    "depends_on_status": "done",
    "created_on": "2026-07-13T11:00:00Z",
}


def test_item_response_deserialises_full_payload():
    item = ItemResponse(**ITEM_JSON)

    assert item.id == 4711
    assert item.work_queue_id == 12
    assert item.title == "Ingest inbox/report.pdf"
    assert item.description == "files-inbox ingestion card"
    assert item.status == "backlog"
    assert item.published is False
    assert item.priority == 0
    assert item.session_template_id == "guideline-extraction"
    assert item.session_id == "sess-0001"
    assert item.assigned_worker_id == "worker-7"
    assert item.input_data == {"file_id": "file_550e8400", "path": "inbox/report.pdf"}
    assert item.saved_state == {"checkpoint": 3}
    assert item.result_data == {"notes": 17}
    assert item.metadata == {"filename": "report.pdf", "size_bytes": 12345}
    assert item.started_on == "2026-07-13T12:01:00Z"
    assert item.created_on == "2026-07-13T12:00:00Z"
    assert item.modified_on == "2026-07-13T12:02:00Z"


def test_item_response_omitted_optional_fields_default_to_none():
    item = ItemResponse(**MINIMAL_ITEM_JSON)

    assert item.description is None
    assert item.session_template_id is None
    assert item.agent_id is None
    assert item.session_id is None
    assert item.assigned_worker_id is None
    assert item.input_data is None
    assert item.saved_state is None
    assert item.result_data is None
    assert item.metadata is None
    assert item.dependencies is None
    assert item.started_on is None
    assert item.blocked_on is None
    assert item.unblocked_on is None
    assert item.completed_on is None


def test_item_response_carries_typed_dependencies():
    item = ItemResponse(**{**MINIMAL_ITEM_JSON, "dependencies": [DEPENDENCY_JSON]})

    assert item.dependencies is not None
    dependency = item.dependencies[0]
    assert isinstance(dependency, DependencyResponse)
    assert dependency.depends_on_item_id == 4700
    assert dependency.depends_on_title == "Classify folder"
    assert dependency.depends_on_status == "done"


def test_create_item_result_created_wraps_item():
    result = CreateItemResult(created=True, item=ItemResponse(**ITEM_JSON))

    assert result.created is True
    assert result.item is not None
    assert result.item.id == 4711
    assert result.existing is None


def test_create_item_result_conflict_carries_existing_summary():
    result = CreateItemResult(created=False, existing=ExistingItemSummary(**CREATE_CONFLICT_JSON))

    assert result.created is False
    assert result.item is None
    assert result.existing is not None
    assert result.existing.work_item_id == 4711
    assert result.existing.status == "doing"
    assert result.existing.published is True


def test_add_ref_result_added_has_no_owner():
    result = AddRefResult(added=True)

    assert result.added is True
    assert result.owner_work_item_id is None
    assert result.owner_status is None


def test_item_command_response_deserialises_with_session_id():
    # The _command endpoint's EXISTING server shape (work_queue_dto.rs ItemCommandResponse),
    # transcribed per design §6.0 — commands do NOT return the updated item.
    response = ItemCommandResponse(
        **{"success": True, "message": "Item published successfully", "session_id": "sess-0001"}
    )

    assert response.success is True
    assert response.message == "Item published successfully"
    assert response.session_id == "sess-0001"


def test_item_command_response_omits_session_id_when_absent():
    # session_id is skip_serializing_if'd server-side: absent, not null, for non-dispatching commands
    response = ItemCommandResponse(**{"success": True, "message": "Item unblocked"})

    assert response.success is True
    assert response.session_id is None


def test_add_ref_result_conflict_carries_owner_without_published():
    # add_ref's 409 body is {work_item_id, status} — narrower than create's.
    result = AddRefResult(added=False, owner_work_item_id=4711, owner_status="blocked")

    assert result.added is False
    assert result.owner_work_item_id == 4711
    assert result.owner_status == "blocked"
    assert not hasattr(result, "owner_published")
