# Implementation Plan: Conflict-body envelope parsing (work-queue 409) → rc13

Spec: [spec.md](./spec.md). TDD per workflow.md. The wire change is deterministic (the new
`{error:{details}}` shape + `CONFLICT_RETRY` type are known), so Phase 1 builds + unit-tests
without ①; the live Gate-A verification (Phase 2) is CONDITIONAL on ①'s branch carrying the
envelope + `CONFLICT_RETRY`.

## Phase 1: Envelope parsing + DTOs + test/e2e reshaping [deterministic — no ① needed] [checkpoint: 6ed3dcf]

- [x] Task: Write failing tests (test_work_queue_client.py) — create_item/add_ref 409 WITH
      `error.details` → owner read from `error.details`, `contended=False` (f66a699)
- [x] Task: Write failing tests — 409 with `error.type == "CONFLICT_RETRY"` → contended=True/no owner;
      keep malformed + non-envelope 409 → original `HTTPError` re-raised (f66a699)
- [x] Task: Write failing tests (test_work_queue_dto.py) for `contended: bool = False` on both result
      DTOs and `ExistingItemSummary.published: bool | None` (a211a7a)
- [x] Task: Implement — `_parse_conflict` reads `error.details` + `error.type` (`_CONFLICT_RETRY_TYPE`);
      create_item/add_ref handle details / CONFLICT_RETRY / re-raise; `contended` on both DTOs;
      `ExistingItemSummary.published` optional (a211a7a + f66a699)
- [x] Task: Update examples/quick_work_queue.py — raw-409 assertions flipped flat → envelope+error.details (57700ba)
- [x] Task: Verify — 186 passed; work_queue_client + work_queue_dto 100% cov; mypy strict / black / bandit clean
- [x] Task: Conductor - User Manual Verification 'Phase 1: Envelope parsing + DTOs' (Protocol in workflow.md) — user-confirmed; report in the 6ed3dcf git note (6ed3dcf)

## Phase 1.5: Queue-scoped `_by_ref` (① `55f9f1f5`) — MUST-FIX [deterministic — no ① needed] [checkpoint: e212a33]

Rc12's `get_item_by_ref` silently breaks against new ①: the org-level path is gone → router 404 →
mis-mapped to "no owner". Replace with the queue-scoped list lookup (spec FR5).

- [x] Task: Write failing tests (test_work_queue_client.py) — `get_items_by_ref` hits
      `/{queue_id}/items/_by_ref` and `/_/items/_by_ref`; returns `list[ItemResponse]`; empty → no
      owner; 404 PROPAGATES; `get_item_by_ref` removed (no alias) (37a15ea)
- [x] Task: Implement — `get_items_by_ref(organisation_id, *, queue_id=None, ref_type, ref_value) ->
      list[ItemResponse]` (scope `_`/id via `_request().json()`; no 404 catch); retired org-wide →
      queue-scoped wording (module header, create_item docstring, by_ref docstring) (37a15ea)
- [x] Task: Update examples/quick_work_queue.py — by_ref → list; cross-queue segment (same ref in two
      queues → both created; `_` → 2, per-queue → 1); delete→CASCADE now asserts `[]` (b5e7e84)
- [x] Task: Verify — 189 passed; work_queue_client + work_queue_dto 100% cov; mypy strict / black / bandit clean
- [x] Task: Conductor - User Manual Verification 'Phase 1.5: Queue-scoped _by_ref' (Protocol in workflow.md) — user-confirmed; report in the e212a33 git note (e212a33)

## Phase 1.6: Name-resolution list endpoints (work_queues + session_templates) [deterministic — enables ③'s by-name job wiring] [checkpoint: e1db2ba]

- [x] Task: Write failing tests (test_work_queue_client.py + test_work_queue_dto.py) — `work_queues().list(org)`
      hits `GET /api/work_queues` with `?o/p/s`, returns `QueuesPagedResult` (Page + `QueueResponse`) (cf3bd26)
- [x] Task: Write failing tests (new test_session_templates_client.py + test_session_templates_dto.py) —
      `session_templates().list(org)` → `SessionTemplatesPagedResult` (Page + `SessionTemplateResponse`) (88ae273)
- [x] Task: Implement — `AgencyWorkQueueClient.list` + `QueueResponse`/`QueuesPagedResult`; new
      `AgencySessionTemplatesClient` + `session_templates_dto.py`; `session_templates()` facade (cf3bd26/88ae273/7e730b7)
- [x] Task: Write failing facade test (test_client_facade.py) for `client.session_templates()` (7e730b7)
- [x] Task: Verify — 201 passed; new modules 100% cov; mypy strict / black / bandit clean; **live sanity vs ①**
      resolved "Guideline Ingestion"→8 and "Guideline Extraction (A)"→98d227ab-…
- [x] Task: Conductor - User Manual Verification 'Phase 1.6: Name-resolution list endpoints' (Protocol in workflow.md) — user-confirmed; report in the e1db2ba git note (e1db2ba)

## Phase 1.7: `_by_ref` merged into `/items` (① follow-up 2026-07-21) [deterministic, live-verified]

① then merged the `_by_ref` route into the paginated `/items` (ref_type/ref_value as query params;
`/items` now accepts `_` for org scope; `_by_ref` route removed). `get_items_by_ref` retargeted.

- [x] Task: Update `get_items_by_ref` — path `/{scope}/items/_by_ref` → `/{scope}/items` with
      `ref_type`/`ref_value`/`s=1000`; parse `body["items"]` (paged envelope) via `_make_request`
      (was bare-list `_request().json()`); return type unchanged (`list[ItemResponse]`); docstring updated
- [x] Task: Reshape TestGetItemsByRef — `/items` path, `s=1000` param, `{page, items}` response;
      miss → empty items (200); 404 propagates
- [x] Task: Verify — 201 passed; work_queue_client 100% cov; mypy strict / black / bandit clean;
      **live e2e re-run vs the restarted ① — ALL STEPS PASSED** (get_items_by_ref → /items: org `_`,
      queue-scoped, cross-queue, empty, CASCADE; publish_verified=True). Pre-flight probe confirmed
      `_by_ref` gone (404), `/items` accepts `_`, paged envelope, miss→empty page.
- [x] Task: Conductor - User Manual Verification 'Phase 1.7: _by_ref→/items' — verified via the live e2e re-run (ALL STEPS PASSED vs restarted ①) + ③'s local integration; folded into the rc13 release

## Phase 2: Live e2e vs ① (Gate A) [DONE — ALL STEPS PASSED vs ① 55f9f1f5] [checkpoint: 3453278]

- [x] Task: Run quick_work_queue.py against ①'s enveloped-409 branch — ALL STEPS PASSED vs
      `localhost:13001` (① `55f9f1f5`). Envelope-409 owner from `error.details` (create + same-queue
      add_ref); org `_` list + queue-scoped isolation + cross-queue 5b (`_`→2, per-queue→1) + delete
      CASCADE. Pre-flight probe confirmed the real 409 body is the `error.details` envelope (the
      openapi's `ConflictErrorResponse` was a dead/misleading schema → ① openapi fix LANDED 2026-07-20
      & re-verified: 409s now bind to `ItemConflictEnvelope`/`AddRefConflictEnvelope`, dead schema
      removed; the wire body is UNCHANGED (doc-only fix — real 409 still `error.details`), so ZERO SDK
      impact). One e2e finding fixed (step 8 stale org-wide → same-queue collision, `a6786d1`);
      SDK contract zero-finding. publish = Stage-2 (template not seeded here). (a6786d1)
- [x] Task: Conductor - User Manual Verification 'Phase 2: Local live e2e' (Protocol in workflow.md) — user-confirmed; report in the 3453278 git note (3453278)

## Phase 3: Release rc13 [Gate B] (the _by_ref→/items merge landed during rc13 dev, pre-release — no version skipped)

- [x] Task: Full suite green (pytest agency_sdk/test/) + bandit clean; bump 0.0.1rc12 → 0.0.1rc13 (pyproject.toml)
- [x] Task: Publish rc13 — PR #12 merged to main (`1125d6d`); tag `v0.0.1rc13` pushed → CI
      "Build and Publish to PyPI" run 29965083524 SUCCESS → **`0.0.1rc13` live on PyPI** (③ swaps
      editable→`==0.0.1rc13` pin; ②'s tag→publish ran parallel with ①'s deploy)
- [x] Task: Conductor - User Manual Verification 'Phase 3: Release' — user-directed release; publish
      workflow green + confirmed on PyPI (latest = 0.0.1rc13)
