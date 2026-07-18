# Track: AgencyWorkQueueClient — work-queue SDK delegate for files-inbox ingestion (gts-agency_python-sdk)

> Track ② of the 3-repo `files_inbox_ingestion_20260713` effort. Python SDK side.
> Design of record (in the guideline-agent repo): `docs/dbq/files-inbox-ingestion-design-20260712.md`
> §6 (SDK delegate) + §5 (the ① server contract it mirrors). Consumed by Track ③ (guideline-agent).

## Overview

Add a work-queue delegate to the SDK so the guideline-agent (Track ③) can drive the files-ingestion
work queue: create items with external refs, publish, claim by ref (`add_ref`), look up by ref, and
run item commands (unblock / retry / reprocess) + item delete. Mirrors ①'s frozen contract; a thin
HTTP client over `/api/work_queues` following the existing `AgencyFilesClient` pattern. Ends by
publishing a new SDK rc (rc11 → rc12) that ③ locks onto.

## Cross-repo coordination

② mirrors ①'s contract (design §5/§6), so it can be BUILT + stub-tested now, in parallel with ①. The
**live e2e + rc publish** are gated on **① merged + deployed**. ③ integration is gated on ②'s rc.
Note: ①'s Phase 0 verification reshaped the contract (added a `retry` command and item DELETE, and the
richer 409 bodies) — those are reflected below.

## Functional Requirements (design §6, incorporating ①'s Phase 0 reshaping)

- **FR1 DTOs** (`agency_sdk/delegates/work_queue_dto.py`, pydantic, snake_case, reuse `Page` from
  `datasets_dto`): `ItemResponse` (id, work_queue_id, status, published, input_data, result_data,
  session_id, …), `CreateItemResult` (created: bool; item | existing={work_item_id, status,
  published}), `AddRefResult` (added: bool; owner_work_item_id, owner_status).
- **FR2 Delegate** (`agency_sdk/delegates/work_queue_client.py`,
  `AgencyWorkQueueClient(BaseDelegateClient)`, `api_path="/api/work_queues"`):
  - `create_item(queue_id, org, *, title, session_template_id, input_data, external_refs=None,
    metadata=None) -> CreateItemResult` — **201 → created; 409 → catch `requests.HTTPError`, return
    created=False + existing** (base `_make_request` raises on 4xx).
  - `publish_item(queue_id, item_id, org) -> ItemResponse`.
  - `add_ref(queue_id, item_id, org, *, ref_type, ref_value) -> AddRefResult` — **201 → added; 409 →
    owner_work_item_id + owner_status**.
  - `get_item_by_ref(org, *, ref_type, ref_value) -> ItemResponse | None` — **org-scoped** (not
    queue-scoped); 404 → None.
  - `get_item(queue_id, item_id, org) -> ItemResponse`.
  - `item_command(queue_id, item_id, org, command, **kw) -> ItemResponse` — generic passthrough for
    unblock / retry / reprocess.
  - `delete_item(queue_id, item_id, org) -> None` — the "full forget" endpoint.
- **FR3 Facade registration**: `AgencyClient.__init__` builds `self.work_queue_client`; add a
  `work_queues() -> AgencyWorkQueueClient` accessor (mirror `files()`).
- **FR4 Release**: bump `0.0.1rc11` → `0.0.1rc12`; publish so ③ can lock onto it.

## Non-Functional Requirements

- TDD with the `stub_requests` fixture (mirror `test_files_client.py` / `test_files_dto.py`); no live
  server in default CI. `pytest agency_sdk/test/`, `bandit` clean.
- Zero behavioural change to existing delegates; purely additive.
- 409-as-control-flow: both `create_item` and `add_ref` MUST catch `requests.HTTPError` for 409 and
  return a typed result, never re-raise (the base client raises by default).

## Acceptance Criteria

- Unit (CI, stubbed): create_item 201 → created; create_item 409 → created=False + existing
  {work_item_id, status, published}; add_ref 201 → added; add_ref 409 → owner id + status;
  get_item_by_ref 404 → None; item_command/delete_item forward correctly; facade `.work_queues()`.
- Live e2e (opt-in, BLOCKED-ON ① deployed): the delegate round-trips against the local stack — create
  → 409 dup → add_ref → _by_ref → unblock/retry/reprocess/delete.
- rc12 published; ③'s dependency lock can resolve it.

## Out of Scope

- Server implementation (①) and agent code (③).
- Any delegate method not needed by ingestion (board views, comments, dependencies, transitions).
