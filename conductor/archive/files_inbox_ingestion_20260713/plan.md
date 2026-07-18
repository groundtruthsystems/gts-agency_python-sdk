# Implementation Plan: AgencyWorkQueueClient (gts-agency_python-sdk)

Design of record: guideline-agent `docs/dbq/files-inbox-ingestion-design-20260712.md` §6.
TDD per workflow.md (pytest + the `stub_requests` fixture). Phases 1–2 build against ①'s frozen
contract; Phase 3 release is gated on ① merged + deployed.

## Phase 1: DTOs [checkpoint: c5e8a89]

- [x] Task: Write failing tests (test_work_queue_dto.py) for ItemResponse / CreateItemResult / AddRefResult (field presence, 201-vs-409 shapes, Page reuse — N/A: no paged endpoint in scope, Page unimported) (8b1921e)
- [x] Task: Implement agency_sdk/delegates/work_queue_dto.py (8b1921e)
- [x] Task: Conductor - User Manual Verification 'Phase 1: DTOs' (Protocol in workflow.md) — user confirmed via '继续Phase 2' (c5e8a89)

## Phase 2: AgencyWorkQueueClient delegate + facade [checkpoint: 0318a6d]

- [x] Task: Write failing tests (test_work_queue_client.py, stub_requests) for create_item — 201→created, 409→created=False+existing{work_item_id,status,published} (HTTPError caught, not raised) (0d6ce3d)
- [x] Task: Write failing tests for add_ref (201→added, 409→owner_work_item_id+owner_status); publish_item; get_item; org-scoped get_item_by_ref (404→None); item_command (unblock/retry/reprocess passthrough); delete_item (0d6ce3d)
- [x] Task: Implement AgencyWorkQueueClient (api_path=/api/work_queues) with all methods + the 409-catch control flow (0d6ce3d)
- [x] Task: Write failing tests (test_client_facade.py) for AgencyClient.work_queues() + work_queue_client construction (d175ee1)
- [x] Task: Register the delegate in agency_sdk/client.py (self.work_queue_client + work_queues() accessor) (d175ee1)
- [x] Task: Conductor - User Manual Verification 'Phase 2: Delegate + facade' (Protocol in workflow.md) — combined with Phase 2b, user-confirmed (0318a6d)

## Phase 2b: Correct the command return type to agency's actual shape [contract fix 2026-07-16] [checkpoint: 0318a6d]

Risk #1 in context.md ("commands return the updated item, but the server returns
ItemCommandResponse") is **resolved in the server's favour**: gts-agency owns `/api/work_queues`, so
its existing shape is a fact, and an unverified line in the guideline-agent design doc must not drive
a server API change (design §6.0). Track ① will NOT reshape it. Nobody consumes the body anyway —
the agent's dispatcher calls `publish_item(...)` as a bare statement and discards the result, and
never calls `item_command` at all. So the SDK conforms.

- [x] Task: Write failing tests — `publish_item` / `item_command` return `ItemCommandResponse`
      {success, message, session_id?}, parsed from the real server shape (not `ItemResponse`) (95b2e8b)
- [x] Task: Add the `ItemCommandResponse` DTO to work_queue_dto.py; change `publish_item` /
      `item_command` return types in work_queue_client.py; update the existing tests that assert an
      ItemResponse comes back from a command (95b2e8b)
- [x] Task: Update context.md — retire e2e risk #1 (resolved by conforming, not by an alarm) and
      record the rule: an EXISTING agency endpoint's shape is transcribed, never asserted (6007024)
- [x] Task: Conductor - User Manual Verification 'Phase 2b: Command return type' (Protocol in workflow.md) — combined with Phase 2, user-confirmed (0318a6d)

## Phase 3A: Live e2e vs the LOCAL stack [GATE A: ①'s BRANCH built into the local docker stack — no merge needed] [checkpoint: 8502a49]

**Two-gate order (2026-07-16): agency is a shared platform, so the e2e runs against a LOCAL
deployment of ①'s feature branch first** (`gts-agency/docker/docker-compose.yaml`; ①'s plan
Phase 4 is that gate). ③ consumes this repo during Gate A as a **local editable install** — no
publish needed; anything the e2e surfaces gets fixed on ①'s branch BEFORE it merges.

**This track's place in the Gate-A choreography (①'s plan Phase 4 owns the full picture):**

```
Stage 0  ① stands the stack up from its BRANCH   <- THIS TRACK IS BLOCKED HERE, and on nothing else
Stage 1  ② THIS e2e — runs FIRST                 <- seconds, no LLM: the cheapest bug-flush
Stage 2  ③ full chain (minutes, real LLM)        <- may parallel this once it has passed once
Stage 3  ① rebase → re-test → PR
```

**Why this track goes first:** every server fix costs a Rust rebuild + image rebuild + stack
restart (minutes). That loop belongs behind a seconds-long contract test, not behind a 10-minute
DBQ extraction. **Findings are fixed on ①'s BRANCH, not worked around here** — then rebuild and
re-run. Nothing merges or publishes during Gate A.

**What Stage 1 is really testing:** this is **the first time the flat 409 leaves the unit tests**.
`_conflict_body` does `response.json()` and expands the top-level keys straight into a pydantic
model — if ①'s handler emits the standard error envelope instead of the flat domain object, this
is where it surfaces (loudly: `ValidationError` / `KeyError`). Nothing earlier can catch it: every
repo's unit tests stub the flat shape. Assert the shape, do not defensively parse around it.

- [x] Task: Live e2e vs the LOCAL stack (opt-in) — create → 409 dup (**FLAT body, assert it is not
      the `{"error":{...}}` envelope**) → add_ref → 409 owner → `_by_ref` (org-scoped, cross-queue)
      → unblock/retry/reprocess (**ItemCommandResponse**, per the resolution above) → delete;
      capture evidence — `examples/quick_work_queue.py`, ALL STEPS PASSED vs `localhost:13001`; both
      flat 409s evidenced (crown jewels). publish dispatch is Stage-2 (blocked >30s, tolerated);
      commands' ItemCommandResponse parse stays unit+schema-verified. Evidence in the (4f77ae6)
      git note (4f77ae6)
- [x] Task: Conductor - User Manual Verification 'Phase 3A: Local live e2e' (Protocol in workflow.md) — user-confirmed (maintain as-is, publish=Stage-2); report in the 8502a49 git note (8502a49)

## Phase 3B: Release rc12 [DONE 2026-07-18 — rc12 PUBLISHED]

Publishing to PyPI is an outward act. Per the 2026-07-18 release flow, ②'s role is **tag → publish**,
run in PARALLEL with ①'s merge+deploy; ③ then pin-swaps to `==0.0.1rc12`, merges, and runs the
real-platform A2/B acceptance (which depends on ①-deploy + ②-publish + ③-pin). So ②'s release gate is
"publish," not "verify against the shared platform" — that verification is ③'s downstream A2/B.

- [x] Task: Re-run the e2e against the SHARED deployment — REASSIGNED to ③'s real-platform A2/B
      acceptance per the 2026-07-18 release flow; not a ② deliverable (② proved the contract live in
      Phase 3A and inside ③'s worker under load)
- [x] Task: Full suite green (pytest agency_sdk/test/) + bandit clean; bump version 0.0.1rc11 →
      0.0.1rc12 (pyproject.toml + any self-version refs) — done ahead of the e2e tasks at the
      user's instruction; publish stays Gate-B-gated (a982736)
- [x] Task: Publish rc12 (so ③ can swap its editable install for the pin) — PR #10 merged to main
      (`86cb337`); tag `v0.0.1rc12` pushed → CI "Build and Publish to PyPI" run 29650678800 SUCCESS;
      rc12 on PyPI (bundles both delegates — session-delegate own-rc guardrail overtaken by the merge)
- [x] Task: Conductor - User Manual Verification 'Phase 3B: Release' — user-directed release ("直接推送发布");
      publish workflow green; ③ pin-swap is the downstream consumer step
