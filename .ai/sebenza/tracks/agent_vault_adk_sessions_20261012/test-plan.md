# Test Plan: Agent Vault — GTS-backed Google ADK 2 sessions (SDK half)

## Strategy
Most proof runs offline:
- An **in-process fake vault** implements the design contract:
  - per-key `version`, `if_version`→412, `if_absent`→409
  - prefix list, `scope=app|user`, correlation lookup
  - a HITL resource with first-wins `_respond`, a fake expiry hook and a write log
  - a **deterministic barrier** that holds N callers after their read and before their write. The race tests (TS-17, TS-41) use it and never sleep.
- A scripted `BaseLlm` subclass drives real ADK Runners.
- Cross-process and SIGKILL claims run as subprocesses sharing a file-backed fake.

The same flows re-run opt-in (`-m integration`) against local comand. They skip, naming the gap, when the TS-40 probe finds the contract missing. Security scenarios assert on observable output: logs, `repr`, request bytes and exceptions.

Not tested here:
- real-model behaviour
- wall-clock performance (call counts are asserted instead)
- backend CAS atomicity, comand's expiry job and the human-authorisation policy (all owned by the backend track)

The SDK has no client-supplied answer path, so TS-63 asserts that the path is absent.

## Test Levels

| Level | Scope | Tooling | Where it runs |
|---|---|---|---|
| Unit | `VaultStore`, head/event codec, HITL DTOs, error mapping, redaction, classification, size caps, retry/timeout policy | pytest, monkeypatched `requests`, in-proc fake | CI, offline (default) |
| Contract (ADK) | `GtsSessionService` vs installed `BaseSessionService` abstract set/signatures; version range; private hooks | pytest + `inspect` | CI `[dev,adk,observability]` job |
| Component (offline) | Real ADK Runner + `DurableRunner` + `GtsSessionService` over the fake; fake LLM; multiple instances; barrier races | pytest, `asyncio.run` | CI, offline |
| Multi-process (offline) | Cross-process round-trip, SIGKILL resume, pause/exit/resume | pytest + `subprocess`, file-backed fake, file counter | CI, offline (POSIX) |
| Integration vs comand | Same flows + contract probe + backend-owned behaviours (first-wins 409, expiry, scope isolation, human identity) | pytest `-m integration`, `AGENCY_*` env, `docs/local_e2e.md` | Local stack; CI only if OQ-2 yes |
| Security / static | TM-06/07/11/14 assertions; `bandit -r agency_sdk/` | pytest `caplog`, bandit | CI |
| Docs / examples | README quick-start and both examples | pytest against the fake; examples manually vs local comand | CI offline / local |

## Scenarios

| TS | Covers | Level | Pri | Given / When / Then | Evidence |
|---|---|---|---|---|---|
| TS-01 | UC-01 | Component | P0 | Same agent+script under InMemory then Gts services → final events and state equal | Equal `model_dump` of both sessions |
| TS-02 | UC-01; R: heavy `[adk]` extra | Unit/subprocess | P1 | `gts://` resolves via ADK registry to `GtsSessionService`; `import agency_sdk` leaves `google.adk` unloaded | Type assert; child `sys.modules`; ImportError names extra |
| TS-03 | UC-02 | Unit | P0 | `create_session` without id → template name resolved via `list`; `register` gets correlation id and labels | Recorded request; id returned; head rev 0 |
| TS-04 | UC-02 (adopt, errors) | Unit | P0 | Unused id → adopted via `update(labels=)`, no register; existing id → `AlreadyExistsError`; unknown template → typed error | No register call; typed exceptions |
| TS-05 | UC-03 | Unit | P0 | Append event with delta incl. `temp:` → event `if_absent` then head `if_version`; marker = `str(version)` | Write-log order; head fields; marker |
| TS-06 | UC-03a; TM-03 | Unit | P0 | Head moved by another writer → `StaleSessionError`; in-memory session, marker, scoped state, head untouched | Exception; memory equals pre-call; no scope PUT |
| TS-07 | UC-03a (retry) | Unit | P1 | Conditional PUT retried after connection error, then 412 with head holding our event → append succeeds once | One logical event; no exception |
| TS-08 | UC-03b | Unit | P1 | `temp:` keys in delta → in memory only; absent from stored head, event and scope writes | Fake contents lack `temp:` |
| TS-09 | UC-04; brief (a) | Multi-process | P0 | Process A writes session (bytes, sets, floats, None fields, state); process B reads → exactly equal | `model_dump(by_alias)` equality |
| TS-10 | UC-04; brief (a) | Integration | P0 | TS-09 against local comand, two processes | Equality; skip reason if probe fails |
| TS-11 | UC-04 | Unit | P1 | 10 events; `num_recent_events=3` or `after_timestamp` → matching tail, in order, via bounded walk | Event ids; GETs ≤ 3 + head |
| TS-12 | UC-04a | Unit | P0 | Unknown id, or known id under another app/user → `None`, no raise | Return `None` |
| TS-13 | UC-04 (schema) | Unit | P1 | Head with future `schema` → typed error, never partial data | Exception type |
| TS-14 | UC-05 | Unit | P0 | Sessions across apps/users; list by app (and user, via policy-filtered `search`); delete one twice → only matches listed; tombstoned, `get` returns `None`; keys kept | Search body; tombstone; keys |
| TS-15 | UC-06 | Component | P0 | `app:`/`user:` written in session 1 via `?scope=` → visible in session 2; survive session-1 delete; `get_user_state` behaviour per OQ | State values; scoped params |
| TS-16 | UC-07 | Component | P0 | Correlation id unknown / in-progress / awaiting-all-pending / failed → create / hydrate / `awaiting_input` / `failed` | `RunOutcome.status`; write log |
| TS-17 | UC-07a; R: contention | Component | P0 | Barrier holds two runners after head read, then releases → one wins; loser `conflict`, writes nothing further | One head lineage; loser writes = 0 |
| TS-18 | UC-07a (register dup) | Unit | P1 | `register` 409 + existing id → re-lookup, resume or conflict; never a second session | One session; typed outcome |
| TS-19 | UC-07b; brief (b) | Multi-process | P0 | 5-step run with counting tool SIGKILLed at step 3; rerun same correlation id → finishes, every counter exactly 1 | File counters; same session id |
| TS-20 | UC-07b; brief (b) | Integration | P0 | TS-19 against local comand | Counters all 1; skip if probe fails |
| TS-21 | UC-07b; R: outage | Unit | P1 | Read timeout or 5xx mid-run → not retried; run raises, stays `IN_PROGRESS` at last checkpoint; later run resumes | One request per failure; head at last good revision |
| TS-22 | UC-08 | Component | P0 | Long-running call → `hitl.create` with `request_id`, `invocation_id`, `expires_at`; no status update at pause beyond pre-run `IN_PROGRESS`; returns `awaiting_input` | POST body; outcome; update log |
| TS-23 | UC-09 | Component | P0 | Request answered in fake comand; `resume(correlation_id)` → stored FunctionResponse injected, run completes, COMPLETED | Final event; no response arg |
| TS-24 | UC-08, UC-09; brief (c) | Multi-process | P0 | Process 1 pauses and exits; fake answers; process 2 `resume(cid)` → agent finishes with correct output | Both exit 0; final state |
| TS-25 | UC-08, UC-09; brief (c) | Integration | P0 | TS-24 via `examples/quick_durable_hitl.py`; answer posted by a separate human-token client | Exit 0; recorded responder |
| TS-26 | UC-09a; TM-05 | Component | P0 | `resume` twice, or two simultaneous after answer → FunctionResponse event committed once; tool not re-run | Counter unchanged; one FR event |
| TS-27 | UC-09a; TM-05 (replay) | Component | P0 | Answer injected, process died pre-commit; fake offers a differing second payload → resume uses comand's stored response | Event holds first payload; counter 1 |
| TS-28 | UC-09b | Unit | P0 | Unknown session, unknown request id, or mismatched call/invocation id → `SessionNotFound`/`RequestNotFound`, zero writes | Typed exceptions; empty write log |
| TS-29 | UC-09c; TM-12 | Component | P0 | Fake expiry hook expires request (410) while local clock says valid → `resume` raises `HitlExpired`; no run | Exception; counter 0 |
| TS-30 | TM-02 | Unit | P1 | Oversize answer rejected; injection-style text reaches agent only as a FunctionResponse part | Typed size error; part type |
| TS-31 | TM-06 | Security | P0 | Sentinel PHI in args, events, `user_id`, correlation id; run, fail, `repr`/`str` → sentinel in no log, span, exception, `repr` | `caplog`, spans, strings: zero matches |
| TS-32 | TM-07 | Unit | P0 | Each key kind written → fixed classification; app `internal` only on opt-in; public/internal elsewhere raises | Request params; `ValueError` |
| TS-33 | TM-08 | Unit | P1 | Miss and foreign hit on `find_by_correlation_id` → both `None`, identical shape and logs | Equal results/logs |
| TS-34 | TM-09, TM-10 | Unit | P0 | Scoped calls carry only `scope=app\|user`, never a user/app id; no request carries `reveal`; no API accepts a scope user | Params/URL; signature check |
| TS-35 | TM-11 | Security | P0 | Malformed, wrong-typed or oversize stored JSON → typed decode error; no pickle/eval/exec in `agency_sdk/adk/` | Exception; bandit clean |
| TS-36 | TM-13; R: head growth | Unit | P1 | Oversize event, oversize head state, or session past event bound → refused before any PUT | Typed error; zero requests |
| TS-37 | TM-14 | Security | P1 | Fake token/secret; log, trace, `repr` clients → neither appears | Zero matches |
| TS-38 | TM-04 | Component | P1 | Answered record carries `responder`, `answered_at` → exposed read-only on `RunOutcome`; foreign request id cannot resume another | Fields; typed error |
| TS-39 | R: ADK churn | Contract | P0 | Installed ADK inspected → every abstract method implemented, compatible signature; version in `>=2.11,<3` | Empty missing-set; hooks present |
| TS-40 | R: backend unbuilt | Integration | P0 | Probe comand: `version`, 412/409, prefix, `scope`, lookup POST, HITL create/respond/410 → match or suite skips naming gap | Probe result; skip message |
| TS-41 | R: CAS contention | Component | P1 | Barrier releases N appends on one session → bounded retries; each succeeds or Stale; no accepted event lost | Attempts ≤ bound; chain consistent |
| TS-42 | R: orphans | Unit | P1 | Crash between event PUT and head CAS → orphan ignored on read; next append succeeds | Read equals pre-crash |
| TS-43 | R: latency budget | Component | P2 | 50-event session → checkpoint = 2 PUTs; full hydrate = 1 prefix list + N GETs | Request counts |
| TS-44 | Impact: additive core | Unit | P0 | Existing callers on old signatures → wire bytes unchanged; new kwargs default off | Existing tests green; byte equality |
| TS-45 | R: retry policy | Unit | P1 | Connection error → retried with backoff; read timeout/5xx → not retried; conditional writes pass `retry=True`; `timeout=10` | Request counts per class |
| TS-46 | Brief (d) | Docs | P1 | README quick-start and both examples run against the fake → assertions pass | Tests pass |
| TS-47 | UC-08 (status); core | Unit | P1 | Session client has `attach`/`update`/lookup, still no `register`; `update(labels=)` envelope correct | Request body; attribute absent |
| TS-48 | UC-07c | Component | P0 | Completed run; new process calls `run` with same cid, different message → stored final result; LLM not called | `already_completed`; zero LLM/PUT |
| TS-49 | UC-07c (recovery) | Component | P1 | Final event committed, status not COMPLETED (killed) → `run` posts COMPLETED, no LLM call | Status update; zero LLM calls |
| TS-50 | UC-07d; TM-06 | Component | P0 | Tool raises with PHI sentinel in message → `adk.failure` has type/ids/counts only, written before FAILED; history kept | Failure bytes lack sentinel; order |
| TS-51 | UC-07c, UC-07d; TM-12 | Component | P1 | Runs reach COMPLETED and FAILED → no vault key deleted by SDK; app/user scope intact | Key set before = after |
| TS-52 | UC-07, UC-09 | Unit | P0 | Answered request, unfinished invocation, caller message all present → resumes with answer; else unfinished id; else message | Input chosen per case |
| TS-53 | UC-08 | Component | P0 | Two long-running calls pending → two requests; one answered → `awaiting_input`; both answered → both injected once, completes | Two creates; counters 1 |
| TS-54 | UC-09c; TM-12 | Integration | P0 | Backend-owned: request past `expires_at` → comand's background job expires it, session FAILED; `resume` raises `HitlExpired` | Server status; audit event + alert emitted; blocked note if absent |
| TS-55 | UC-04; TM-11 | Unit | P0 | Event with `inline_data` bytes → stored base64 JSON, loaded via `model_validate_json` → bytes exactly equal | Byte equality; no `exclude_none` |
| TS-56 | TM-09 | Integration | P0 | Two users' sessions, same app → user scope sees only own state; unbound owner → 4xx; no redirection to another user/app | Distinct values; 4xx |
| TS-57 | UC-06; TM-03 | Unit | P1 | Crash after head commit before scope merge → next **run** re-applies delta; `get_session` never writes; after Stale, no scope write | Scope value; write log |
| TS-58 | TM-11 | Unit | P0 | Cyclic or overlong `prev_key` chain → typed error; walk terminates within bound | Exception; steps ≤ bound |
| TS-59 | R: hydrate cost | Unit | P1 | Hydrate 40 events → parallel GETs, peak concurrency ≤ 8 | Peak in-flight counter |
| TS-60 | R: thread safety | Unit | P1 | Concurrent `to_thread` calls with expired token → one token fetch, all use it | One auth request |
| TS-61 | R: cancellation | Component | P1 | Task cancelled during head PUT → write completes (shielded); head re-read awaited before any write; no duplicate event | One event; head consistent |
| TS-62 | TM-08, TM-06 | Security | P0 | Lookup a correlation id; force `requests` error → id only in POST body; exception and logs contain no URL or id | Request URL; exception text |
| TS-63 | TM-01 | Unit | P0 | `DurableRunner.resume` has no response parameter; runner never calls `hitl.respond`; answers read only from comand | `inspect`; call log empty |
| TS-64 | UC-09a; TM-05 | Integration | P1 | Backend-owned: two humans `_respond` → first 200, second 409; stored response first; re-dispatch payload holds only session id | Status codes; record; dispatch body |
| TS-65 | TM-01, TM-04 | Integration | P1 | Answered with human token A → `responder` is A; worker's client token on `_respond` → rejected | Record field; 403 |
| TS-66 | UC-08 (crash window) | Component | P0 | Pause event committed, process killed before `hitl.create` → next run creates the request (idempotent), returns `awaiting_input`, no re-run | One request; counter unchanged |
| TS-67 | UC-07, UC-09 | Component | P1 | Fresh run, in-progress resume, and AWAITING_INPUT resume with answers → worker posts `IN_PROGRESS` before running; any expired request → `HitlExpired` first | Update order; exception |
| TS-68 | TM-09 | Unit | P0 | Fake returns 4xx for `scope=user` on an unbound owner → typed error, no fallback to app scope; register/adopt send the owner/dispatch claim | Exception; no app-scope call; request body |
| TS-69 | UC-07d | Component | P1 | Crash after `adk.failure` before FAILED → rerun rewrites nothing (idempotent) and posts FAILED; rerun after FAILED → `failed`, no LLM call | Record unchanged; zero LLM calls |
| TS-70 | TM-01 | Unit | P1 | Fake models comand policy: `_respond` with the worker's client token → 403 surfaced as typed error; nothing recorded | Exception; record still pending |
| TS-71 | UC-05, UC-07 | Unit | P1 | Correlation id resolves to a tombstoned session → `run` raises `SessionNotFound`, no writes | Exception; empty write log |

## Traceability

| Use case / flow | Scenarios |
|---|---|
| UC-01 | TS-01, TS-02 |
| UC-02 | TS-03, TS-04 |
| UC-03 | TS-05 |
| UC-03a | TS-06, TS-07 |
| UC-03b | TS-08 |
| UC-04 | TS-09, TS-10, TS-11, TS-13, TS-55 |
| UC-04a | TS-12 |
| UC-05 | TS-14, TS-71 |
| UC-06 | TS-15, TS-57 |
| UC-07 | TS-16, TS-52, TS-67, TS-71 |
| UC-07a | TS-17, TS-18 |
| UC-07b | TS-19, TS-20, TS-21 |
| UC-07c | TS-48, TS-49, TS-51 |
| UC-07d | TS-50, TS-51, TS-69 |
| UC-08 | TS-22, TS-24, TS-25, TS-47, TS-53, TS-66 |
| UC-09 | TS-23, TS-24, TS-25, TS-52, TS-67 |
| UC-09a | TS-26, TS-27, TS-64 |
| UC-09b | TS-28 |
| UC-09c | TS-29, TS-54, TS-67 |

| Risk / threat | Scenarios |
|---|---|
| TM-01 | TS-63, TS-65, TS-70 |
| TM-02 | TS-30 |
| TM-03 | TS-06, TS-17, TS-41, TS-57 |
| TM-04 | TS-38, TS-65 |
| TM-05 | TS-26, TS-27, TS-64 |
| TM-06 | TS-31, TS-50, TS-62 |
| TM-07 | TS-32 |
| TM-08 | TS-33, TS-62 |
| TM-09 | TS-15, TS-34, TS-56, TS-68 |
| TM-10 | TS-34 |
| TM-11 | TS-35, TS-55, TS-58 |
| TM-12 | TS-29, TS-51, TS-54 |
| TM-13 | TS-36 |
| TM-14 | TS-37 |
| R: retained PHI / erasure (backend release gate) | TS-50, TS-51, TS-54 |
| R: abandoned requests depend on comand's expiry job | TS-29, TS-54 |
| R: ADK churn | TS-39 |
| R: head-CAS contention | TS-17, TS-41 |
| R: hydrate O(n) / 2 round trips | TS-11, TS-43, TS-59 |
| R: backend unbuilt / contract or fake drift | TS-40, TS-10, TS-20, TS-25 |
| R: comand outage (fails closed) | TS-21, TS-45 |
| R: heavy `[adk]` extra / OTel pins | TS-02 + CI job (`pip check`) |
| R: head growth / large inline_data | TS-36 |
| R: orphan events | TS-42 |
| R: prompt injection via answers | TS-30 |
| R: backend CAS atomicity and HITL policy | TS-40, TS-56, TS-64, TS-65 |
| R: scoped LWW repair | TS-57 |
| R: cancellation / detached shielded write | TS-61 |
| Impact: additive core changes | TS-44, TS-45, TS-60 |
| Brief (a) / (b) / (c) / (d) | TS-09, TS-10 / TS-19, TS-20 / TS-24, TS-25 / TS-46 |

## Test Data & Environments
- All data synthetic: sentinels like `PHI-SENTINEL-0001`, fake names, fake correlation ids; no real PHI, PII or credentials.
- Fake vault: in-process contract implementation with a write log, barrier hook, fake expiry hook and in-flight counter; a file-backed `tmp_path` variant for subprocess scenarios.
- Fake LLM: scripted `BaseLlm` with long-running and failing tools; the counting tool persists to a file outside the vault.
- Credentials are placeholders; the integration tier reads `AGENCY_*` env vars and never writes them anywhere.
- Integration tier: local comand per `docs/local_e2e.md`, unique names, unconditional cleanup, and a second human-token client for TS-25/64/65.
- `integration` marker registered, deselected by default; skips on TS-40 probe failure. TS-54/56/64/65 are backend-owned and may be recorded as blocked.
- CI job `[dev,adk,observability]` + `pip check` runs the ADK-contract and lazy-import scenarios.
- SIGKILL scenarios are POSIX-only; tests use `asyncio.run`.

## Entry & Exit Criteria
- Entry: a spike confirms ADK resumability yields a stable, resumable `invocation_id` with the fake LLM (TS-19/24/52).
- Entry: `.[dev,adk,observability]` installs and the existing offline suite is green.
- Entry: each scenario's plan task is written failing-first (Red) before implementation.
- Exit: every **offline** P0 passes; every `UC-nn` and alternate flow (incl. UC-07c/07d) has a passing offline scenario.
- Exit: coverage >80% for `agency_sdk/adk/` and the changed delegate code.
- Exit: `mypy agency_sdk/`, `black --check`, `bandit -r agency_sdk/ -x agency_sdk/test` clean; TS-31/39/55/63 pass.
- Exit: integration P0s (TS-10/20/25/40/54/56) pass against local comand, or are recorded **blocked** on the backend track naming the missing contract; never reported as passed.
- Exit: README, both examples and CLAUDE.md done (TS-46); a manual benchmark records checkpoint and hydrate latency.

## Coverage Map
Omitted: the two traceability tables already show the full fan-out, and a 71-scenario diagram would be less readable than they are.

## Open Questions
1. Does the integration suite run in CI (design OQ-10)? That decides whether the integration P0s gate.
2. The fakes cannot prove comand's CAS atomicity or first-wins 409. Is TS-40/56/64 as the only proof accepted?
3. What are the max value size, event bound, default HITL TTL (design OQ-2, OQ-5)? TS-29/30/36/54 need numbers.
4. Who owns the retention and erasure test (design OQ-4)? Until it exists, TS-51 proves only that the SDK does not delete.
5. `get_user_state` without a session (design OQ-8) changes TS-15's last assertion.
