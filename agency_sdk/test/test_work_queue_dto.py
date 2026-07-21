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
    QueueResponse,
    QueuesPagedResult,
)

QUEUE_JSON = {
    "id": 8,
    "name": "Guideline Ingestion",
    "description": "Files-inbox ingestion queue for the guideline agent (org 2)",
    "status": 1,
    "created_on": "2026-07-20T20:47:33Z",
    "created_by": "901",
    "modified_on": "2026-07-20T20:47:33Z",
}

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
    # add_ref's 409 owner summary is {work_item_id, status} — narrower than create's.
    result = AddRefResult(added=False, owner_work_item_id=4711, owner_status="blocked")

    assert result.added is False
    assert result.owner_work_item_id == 4711
    assert result.owner_status == "blocked"
    assert not hasattr(result, "owner_published")


def test_existing_item_summary_published_is_optional():
    # The enveloped error.details may omit `published` (mirrors details.get("published")).
    summary = ExistingItemSummary(work_item_id=4711, status="doing")

    assert summary.work_item_id == 4711
    assert summary.status == "doing"
    assert summary.published is None


def test_create_item_result_contended_defaults_false_and_can_be_set():
    assert CreateItemResult(created=True).contended is False

    contended = CreateItemResult(created=False, existing=None, contended=True)
    assert contended.contended is True
    assert contended.existing is None


def test_add_ref_result_contended_defaults_false_and_can_be_set():
    assert AddRefResult(added=True).contended is False

    contended = AddRefResult(added=False, contended=True)
    assert contended.contended is True
    assert contended.owner_work_item_id is None


def test_queue_response_deserialises_from_server_json():
    queue = QueueResponse(**QUEUE_JSON)

    assert queue.id == 8
    assert queue.name == "Guideline Ingestion"
    assert queue.status == 1
    assert queue.description == "Files-inbox ingestion queue for the guideline agent (org 2)"
    assert queue.created_on == "2026-07-20T20:47:33Z"
    assert queue.created_by == "901"


def test_queue_response_optional_fields_default_to_none():
    queue = QueueResponse(id=9, name="q", status=1, created_on="t", modified_on="t")

    assert queue.description is None
    assert queue.created_by is None


def test_queues_paged_result_wraps_page_and_items():
    result = QueuesPagedResult(**{"page": {"page": 0, "size": 1, "total": 1}, "items": [QUEUE_JSON]})

    assert result.page.total == 1
    assert [q.id for q in result.items] == [8]
    assert result.items[0].name == "Guideline Ingestion"
