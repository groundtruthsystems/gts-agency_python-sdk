# Spec: Agent Vault — GTS-backed Google ADK 2 sessions (SDK half)

Design: [`design.md`](./design.md) · Tests: [`test-plan.md`](./test-plan.md) · Contract: [`docs/agent_vault_gap_analysis.md`](../../../../docs/agent_vault_gap_analysis.md)

## Overview
ADK 2 agents lose their sessions with the process. This track ships:
- `GtsSessionService`, an ADK 2 session service persisted in the GTS session vault, adopted by configuration only;
- `DurableRunner`, which resumes a run by correlation id and pauses for a human answer recorded in comand.

The comand endpoints are a contract, delivered by the backend track `agent_vault_backend_20261012`. Until they ship, the SDK is proven offline against fakes.

## Functional Requirements

**Packaging and core clients**
- FR-01: An optional `[adk]` extra pins `google-adk>=2.11,<3`. `agency_sdk.adk` is lazy-imported, and `gts://` is registered with ADK's service registry.
- FR-02: The vault client gains a per-key `version`, `set(if_version=, if_absent=)` raising `ConditionalWriteFailed`, `list(prefix=, page=, size=)` and `scope="app"|"user"`.
- FR-03: `_make_request` gains an additive `timeout=`. `CredentialsSupplier` refreshes tokens under a lock with a double check.
- FR-04: The templates client gains `register(org, name, *, correlation_id, labels, input)`, resolving the template name via `list`.
- FR-05: The session client gains `find_by_correlation_id` (POST `_lookup`), `search`, `update(labels=)` and `SessionStatus.AWAITING_INPUT`. It still has no `register`.

**Session storage**
- FR-06: `VaultStore` wraps the sync clients in `asyncio.to_thread`. It maps 409/412 to `StaleSessionError`, shields checkpoints, caps parallel GETs at 8, and scrubs URLs from errors.
- FR-07: `GtsSessionService` implements every abstract `BaseSessionService` method:
  - `create` registers a session or adopts a given id, and raises `AlreadyExistsError` for an existing one;
  - `get` honours ADK's filters;
  - `list` uses `search`;
  - `delete` writes a tombstone.
- FR-08: `append_event` runs in this order:
  1. Trim `temp:` keys.
  2. Write the event with `if_absent`.
  3. Write the head with `if_version`.
  4. Update the in-memory session and set marker = `str(version)`.
  5. Merge the scoped delta.
- FR-09: Events are stored with `model_dump_json(by_alias=True)` and loaded with `model_validate_json`. An unknown head `schema` fails closed.
- FR-10: The `prev_key` walk is bounded and cycle-checked for `num_recent_events`. A full hydrate uses a prefix list plus parallel GETs.
- FR-11: `get_user_state` raises `NotImplementedError`.
- FR-12: Classification is fixed per key kind. `public`/`internal` are rejected, except for app scope with an explicit opt-in.
- FR-13: Configurable caps (1 MiB per value, 10,000 events per session) raise a typed error before any PUT.

**Durable execution**
- FR-14: `DurableRunner.run(correlation_id, user_id, message)` looks up the session, then creates or hydrates it. It picks its input in order: answered request, then unfinished invocation, then the caller's message.
- FR-15: Status handling:
  - completed → stored final result;
  - failed → `failed` or `HitlExpired`;
  - tombstoned → `SessionNotFound`;
  - stale write → `conflict`, with no further writes.
- FR-16: On an agent error, the runner writes a type-only `adk.failure` idempotently, then sets FAILED. Transient comand errors leave the session `IN_PROGRESS`.
- FR-17: The worker posts `IN_PROGRESS` before every `run_async`. A final event that committed without a status is completed without calling the LLM.

**HITL**
- FR-18: `AgencySessionHitlClient` provides `create`, `get` and `list`, plus `respond`, which needs a caller-supplied human token supplier.
- FR-19: A pause creates one request per pending long-running call. `expires_at` defaults to 7 days and is configurable per runner and per run.
- FR-20: `resume(correlation_id)` has no response parameter. It injects comand-stored answers only once all of the invocation's requests are answered, and their call and invocation ids must match.
- FR-21: Typed errors: `HitlExpired`, `SessionNotFound`, `RequestNotFound`. A pause committed without a request is repaired by the next run.

**Docs**
- FR-22: Deliver `docs/adk_sessions.md`, a README quick-start, `examples/quick_gts_session_service.py`, `examples/quick_durable_hitl.py`, and CLAUDE.md delegate notes.

## Non-Functional Requirements
- NFR-01: No payload, `user_id`, correlation id or URL reaches logs, spans, exception text or `repr`.
- NFR-02: The change is additive only; existing signatures and wire bytes are unchanged.
- NFR-03: Retries follow `base_client`. Only connection errors are retried, and conditional writes opt in with `retry=True`. The timeout is 10 s.
- NFR-04: `import agency_sdk` never imports `google.adk`.
- NFR-05: mypy strict, black and bandit are clean, and coverage is above 80% for new and changed code.

## Acceptance Criteria
- AC-01 (FR-01, NFR-04): Switching by configuration works, and the extra stays lazy. TS-01, TS-02, TS-39.
- AC-02 (FR-02–05, NFR-02/03): The core client extensions are additive and correct. TS-33, TS-37, TS-44, TS-45, TS-47, TS-60, TS-62.
- AC-03 (FR-06, FR-09, FR-10, FR-12, FR-13): The store and codec are exact, bounded and classified. TS-07, TS-13, TS-32, TS-35, TS-36, TS-55, TS-58, TS-59, TS-61.
- AC-04 (FR-07, FR-08, FR-11): The session service honours ADK's contract and the commit order. TS-03, TS-04, TS-05, TS-06, TS-08, TS-11, TS-12, TS-14, TS-15, TS-34, TS-41, TS-42, TS-43, TS-57, TS-68.
- AC-05 (FR-07): A session reads back exactly in another process. TS-09 offline, TS-10 integration.
- AC-06 (FR-14–17): A killed run resumes without repeating steps, and terminal states are honoured. TS-16, TS-17, TS-18, TS-19, TS-21, TS-48, TS-49, TS-50, TS-51, TS-52, TS-69, TS-71; TS-20 integration.
- AC-07 (FR-18–21): HITL pauses, resumes once, rejects forged or duplicate answers, and expires. TS-22, TS-23, TS-24, TS-26, TS-27, TS-28, TS-29, TS-30, TS-38, TS-53, TS-63, TS-66, TS-67, TS-70; TS-25, TS-54, TS-64, TS-65 integration.
- AC-08 (NFR-01): The PHI sentinel never escapes. TS-31.
- AC-09 (contract): The backend probe and isolation checks pass against comand, or are recorded as blocked. TS-40, TS-56.
- AC-10 (FR-22): The docs and examples run. TS-46.

## Out of Scope
- Agent frameworks other than ADK 2.
- comand endpoints, the expiry background job, retention policy and OpenAPI (backend track).
- The reviewer UI and work-queue re-dispatch wiring.
- A `get_user_state` endpoint without a session.

## Open Questions
- Is `version` a body field (assumed) or an ETag? The answer changes only `VaultStore`'s mapping.
- What is the numeric code for `AWAITING_INPUT`? It is assumed to be `3` until the backend confirms.
- Which dispatch claim binds the session owner? This affects only `register`/adopt payloads.
- Should the caps be aligned once comand publishes its real limits?
