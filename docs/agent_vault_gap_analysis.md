# Agent Vault: gap analysis (Google ADK 2 sessions on the GTS session vault)

**Status:** Phase 0 deliverable, for review before Phase 1 starts. **Date:** 2026-10-12 (rev 2, after
architecture verification and operator decisions).
**Scope:** the SDK half (`gts-agency_python-sdk`). The backend half (`comand`) is the separate track
`agent_vault_backend_20261012`. §4 below is the contract that track has to implement.
**Design of record:** `.ai/sebenza/tracks/agent_vault_adk_sessions_20261012/design.md`.

**Operator decisions folded into rev 2:**
1. A HITL answer is recorded **by comand under the reviewer's own authenticated identity**. The SDK
   never accepts an answer from a client payload.
2. Abandoned HITL requests are expired by comand's **existing background-job task**. This design specifies
   no sweeper of its own.
3. There are **no new vaults**. `app:`/`user:` state goes through the existing session vault API with a
   `scope`. comand resolves the app and user from the session and enforces backend policy.
4. comand **no longer clears the vault** at terminal status, which makes forensics possible.

## 1. ADK contract (verified against `google-adk==2.11.0`)

The SDK does not depend on ADK today. We will add an optional extra, `[adk]`, pinned to
`google-adk>=2.11,<3` (2.11.0 is the latest 2.x). It is lazy-imported the way `[observability]` is.
Everything below was read from the 2.11.0 wheel source.

### 1.1 `google.adk.sessions.BaseSessionService`

| Member | Kind | Semantics we must honour |
|---|---|---|
| `create_session(*, app_name, user_id, state=None, session_id=None) -> Session` | abstract | `state` may carry `app:`/`user:` keys, which are routed to their scopes. An existing id → `AlreadyExistsError` |
| `get_session(*, app_name, user_id, session_id, config=None) -> Session \| None` | abstract | `None` when absent. `num_recent_events` (≥0) and `after_timestamp` filter events. State merges app, user and session |
| `list_sessions(*, app_name, user_id=None) -> ListSessionsResponse` | abstract | oldest-updated first; sessions come back **without** events or state |
| `delete_session(*, app_name, user_id, session_id) -> None` | abstract | writes a tombstone on the head; reads then return `None`. Physical removal follows the retention policy |
| `get_user_state(*, app_name, user_id) -> dict` | optional | the default raises `NotImplementedError`, which ADK documents as acceptable (open question 8) |
| `append_event(session, event) -> Event` | overridable | skip `partial`. `temp:` is applied in memory and never persisted. May raise `google.adk.errors.StaleSessionError` |
| `flush()` | optional | a no-op, because we write through |

ADK's persistent services keep a revision in the private `Session._storage_update_marker` (a `str`)
and raise `StaleSessionError` on mismatch. We do the same, with `str(head.version)`.

### 1.2 Resume and long-running tools: build on them, do not wrap them

| ADK feature | What it does | How we use it |
|---|---|---|
| `App(resumability_config=ResumabilityConfig(is_resumable=True))` (experimental) | Pauses an invocation on a long-running call. Resumes a paused or failed invocation from its last event. The resumed tool is at-least-once; `temp:` is lost | the basis of UC-07b and UC-08 |
| `Runner.run_async(*, user_id, session_id, invocation_id=None, new_message=None, …)` | A `FunctionResponse` `new_message` is resolved to the paused invocation automatically. **A plain message with no `invocation_id` starts a new invocation**, and `new_message=None` with nothing resumable raises `ValueError` | `DurableRunner` chooses the input explicitly (§5) |
| `LongRunningFunctionTool`, `request_confirmation`, `RequestInput`, `get_user_choice` | Emit events with `long_running_tool_ids`; `InvocationContext.should_pause_invocation` ends the invocation | the pause triggers a HITL request in comand |
| `cli.service_registry.ServiceRegistry.register_session_service` | URI scheme → factory | a `gts://` URI works with **no code change** (UC-01) |

**Decision: build on.** We add what ADK leaves to the host:
- a durable store
- a mapping from correlation id to session
- an identified-human answer channel
- expiry

Tools with side effects must be idempotent, because ADK re-runs a tool that was in flight at a crash.

## 2. Mapping onto the vault

**One ADK session is one GTS session.** The ADK `session_id` *is* the GTS session id. A dispatched
agent adopts the id it was injected with, and `create_session` without an id registers a new session
from a configured template **name**.

### 2.1 Key scheme (charset `[A-Za-z0-9._-]`, ≤255)

| Key | Mutability | Contents |
|---|---|---|
| `adk.session` | mutable, CAS | `schema`, `app_name`, `user_id`, `correlation_id`, `revision`, `event_count`, `last_event_key`, `last_update_time`, session-scoped `state` (JSON-native only) |
| `adk.ev.<seq:08d>.<event_id>` | immutable, `if_absent` | `seq`, `prev_key`, `event` (`Event.model_dump_json(by_alias=True)`, no `exclude_none`) |
| `adk.failure` | write-once | `error_type`, `invocation_id`, `last_event_key`, `revision`, `at` with **no payloads** |
| `adk.state` with `scope=app` / `scope=user` | mutable, CAS | the un-prefixed `app:` or `user:` state. comand resolves the app and user from the session |

HITL requests are **not** vault keys. They are a comand resource (§4, item 6).

**Append (checkpoint), in order:**
1. Trim `temp:` keys from the delta.
2. PUT the event `if_absent`.
3. PUT the head with `if_version`. **This is the commit point.**
4. Only after step 3 succeeds: mutate the in-memory session and set the marker.
5. Apply the `app:`/`user:` deltas through `scope` as a per-key merge, retrying on 412.

A loser at step 3 raises `StaleSessionError` and touches nothing else. Its orphan event is
unreachable. Step 5 sits outside the commit guarantee: if a crash skips it, the next **run** re-applies
the last committed event's scoped delta. That happens on a write path, never in `get_session`, and is
last-writer-wins per key (an accepted limit). Conditional
writes use `retry=True`. A 412 or 409 after a retry is re-read, and if the stored key or id is ours
the write succeeded.

**Read.**
- With `num_recent_events`: GET the head, then walk `prev_key` N times. The walk is bounded and
  checked for cycles.
- With full history: list prefix `adk.ev.`, fetch the values with parallel GETs (semaphore 8), link
  them by `prev_key` from `last_event_key`, and drop orphans.
- Events are loaded with **`Event.model_validate_json`**. ADK sets `val_json_bytes='base64'`, which
  only applies in JSON mode, so `model_validate(dict)` would corrupt `inline_data`.
- Scoped state is merged with its prefixes restored.

### 2.2 Classification (default `restricted`)

| Kind | Classification | Why |
|---|---|---|
| head, events, failure record | `restricted` | PHI-capable content; `user_id` is PII |
| `adk.state` scope=user | `restricted` | PII and PHI-capable |
| `adk.state` scope=app | `restricted`, or `internal` by explicit opt-in | PHI is forbidden by contract |
| HITL request (comand) | `confidential` | reviewers see the prompt through an audited reveal |
| `temp:` | never persisted | ADK contract |

The service rejects `public`/`internal` for every session key; the only exception is the app-scope
opt-in. No payload, argument, event text, `user_id` or correlation id reaches logs, traces,
exceptions or `repr`, and `VaultStore` scrubs URLs from exception text.

### 2.3 Lifecycle

comand no longer clears the vault at terminal status. Consequences:
- History is retained for forensics, so UC-07c can return the stored final result.
- A **retention and erasure policy is now a backend obligation** (open question 4).
- `AWAITING_INPUT` is non-terminal. A request that expires is swept by comand: the request becomes
  `expired` and the session is marked Failed.

## 3. SDK gaps

| Gap | Today | Needed |
|---|---|---|
| Async API | sync `requests` delegates | `VaultStore` over `asyncio.to_thread`, with checkpoints `asyncio.shield`ed |
| Versioned reads | `get` → value | `+version` |
| Conditional writes | unconditional `PUT` | `if_version=`, `if_absent=`; `ConditionalWriteFailed` → `StaleSessionError` |
| Prefix listing | full list | `prefix=`, paging |
| Scoped state | session keys only | `scope="app"\|"user"` on get/set/list |
| Timeouts | hardcoded 30 s | additive `timeout=` on `_make_request` |
| Thread-safe tokens | unlocked `CredentialsSupplier.bearer_token` | a `threading.Lock` with a double-checked refresh |
| Register | raw HTTP in the example | `session_templates().register(org, name, *, correlation_id, labels, input)` |
| Correlation lookup and labels | none | `sessions().find_by_correlation_id`, `search`, and `update(labels=)` |
| HITL | none | `AgencySessionHitlClient`: `create`, `get`, `list`, `respond` (human token); DTOs |
| Status | `FAILED`, `COMPLETED`, `IN_PROGRESS` | `AWAITING_INPUT` |
| ADK adapter | none | `GtsSessionService`, `DurableRunner`, `gts://` factory |
| Batch operations | none | not required, thanks to the head-commit design |

## 4. Backend contract required from `comand`

All endpoints take `?o=<org>`, use bearer auth, and return the standard error envelope.

1. **Versioned vault entries.** `GET`, the `PUT` response and the list `entries[]` all add a
   `version: int` (monotonic per key).
2. **Conditional PUT.** `?if_version=<n>` → **412** on mismatch. `?if_absent=true` → **409** if the
   key exists. Check and write must be atomic per key.
3. **Prefix list.** `GET …/vault?prefix=&p=&s=`, in lexicographic order.
4. **Scoped state on the session vault.** `?scope=app|user` on GET, PUT and list. comand resolves the
   app (from the session's template or agent) and the user (the session's **bound owner**) and applies
   backend policy, so the client cannot name another app or user.
   - The owner is bound at register or adopt, from the authenticated human or a dispatch claim.
   - `scope=user` **fails closed (4xx)** when no owner is bound.
5. **Labels, correlation lookup and search.**
   - `register` and `update` accept `labels: {string: string}`.
   - A `correlation_id` label is unique per org: a duplicate register returns 409 with the existing id
     in `error.details`.
   - `POST /api/sessions/_lookup {correlation_id}` → `{session_id, status, labels}`. A miss and a
     foreign hit both return **404**.
   - `POST /api/sessions/_search {labels, user?}` is filtered by policy.
6. **HITL requests.**
   - `POST /api/sessions/{sid}/hitl {request_id, invocation_id, prompt, expires_at}` (agent). It is
     idempotent on `request_id` and atomically sets the session to `AWAITING_INPUT`. `prompt` is
     stored `confidential`.
   - `GET /api/sessions/{sid}/hitl[?status=]` and `GET …/hitl/{rid}` return `{status, response,
     responder, answered_at, expires_at}`.
   - `POST …/hitl/{rid}/_respond {response}` is called by a **human principal authorised by backend
     policy**. The first answer wins (a later one gets **409**). An expired request returns **410**.
     comand records the verified `responder` and audits the call. **Service principals are rejected**;
     the policy is assignment- or role-based.
   - An optional, authenticated, idempotent re-dispatch hook (work-queue reprocess) carries **only the
     session id**, never the answer.
7. **Expiry (dependency, not a new requirement).** comand's planned background-job task moves pending
   requests past `expires_at` (server time) to `expired` and marks the session Failed, with audit and
   alerting. The SDK only supplies `expires_at` and handles `expired`/410 as `HitlExpired`.
8. **`AWAITING_INPUT` status.** A new non-terminal code (proposed `3`). It does not trigger cost or
   review sampling.
9. **No terminal clear, plus a retention policy.** The vault and HITL records are retained under a
   documented retention and erasure policy, including a subject-erasure route for `scope=user`. This
   policy is a **release gate**.
10. **Limits.** A documented maximum value size and maximum keys per session.
11. *(Optimisation)* `GET …/vault?prefix=&values=true`.

Until these endpoints exist, the SDK codes against this contract behind the client layer. Offline tests
use fakes, and the opt-in integration suite skips when the probe finds a gap.

## 5. Recommendation

| Phase | SDK deliverables | Needs from comand | Acceptance |
|---|---|---|---|
| **1: Session storage** | vault client extensions (version, conditional writes, prefix, scope); `timeout=`; locked tokens; `VaultStore`; `GtsSessionService`; `register`; labels and search; `gts://`; `[adk]`; quick-start | items 1–5, 10 | a session created and updated in one process reads back **exactly** (events, state, bytes) in another |
| **2: Durable execution** | `find_by_correlation_id`; `DurableRunner.run`; resume input selection; `adk.failure`; already-completed result | items 5, 9 | a SIGKILLed run resumes with the same correlation id and repeats no completed step |
| **3: HITL** | `AgencySessionHitlClient`; pause → `hitl.create`; `DurableRunner.resume(correlation_id)`; typed `SessionNotFound`, `RequestNotFound`, `HitlExpired`; example | items 6–8 | pause → process exits → a human answers in comand → the agent finishes |

**Checkpoint.** Every non-partial event handed to `append_event` is a checkpoint, written
synchronously. It is durable once the head CAS commits, and the next model call waits for that.

**Run identity.** A correlation id is **one run**. FAILED and COMPLETED are terminal in comand (its
stale-regression guard), so a retry uses a new correlation id. Transient outages leave the session
`IN_PROGRESS`. On `AWAITING_INPUT`, the runner calls `hitl.list`: if any request is expired →
`HitlExpired`; if all are pending → `awaiting_input`; if all requests of the paused invocation are answered → hydrate, post `IN_PROGRESS`, and continue.
A partially answered invocation stays `awaiting_input`. If `hitl.list` is empty (a crash before
`hitl.create`), the runner hydrates and step 0 creates the requests. A tombstoned session returns
`SessionNotFound`. `adk.failure` is written idempotently: an existing record is kept.

**Resume input selection (`DurableRunner`), in priority order:**
0. The last committed event has `long_running_tool_ids` but no HITL request exists (a crash before
   `hitl.create`) → create the request(s); creation is idempotent.
1. An `answered` HITL request with no `FunctionResponse` event for its call id yet → `new_message` =
   that `FunctionResponse`, using the response comand stored, never a client payload. Its call id and
   invocation id must match the pending request.
2. Otherwise the last invocation with no final response → `invocation_id`.
3. Otherwise, if the final event is committed but the status is not Completed → post Completed.
4. Otherwise the caller's message.

The committed `FunctionResponse` event is the dedup marker, so a duplicate `resume` cannot inject
twice. Concurrent resumes race on the head CAS, and the loser stops.

**Concurrency.** The first writer to commit revision *r+1* wins. A loser gets `StaleSessionError`
and returns `RunOutcome(status="conflict")` without writing.

## 6. Open questions

1. Is `version` a body field (proposed) or an ETag? Are 412 and 409 final?
2. What is the maximum value size and key count? Should `inline_data` spill to `gtsf://`?
3. Is list-with-values in this release?
4. What is the retention period and erasure path for retained vaults and HITL records?
5. What is the default HITL TTL (proposed 7 days), and who is alerted?
6. Which backend policy authorises a human to `_respond`?
7. What dispatch claim carries the end user when a client-credentials worker registers a session?
8. Should `get_user_state` without a session be served by comand, or raise `NotImplementedError`?
9. What is the `AWAITING_INPUT` code? (Decided: the resumed worker posts `IN_PROGRESS`.)
10. Does the integration suite run in CI?
