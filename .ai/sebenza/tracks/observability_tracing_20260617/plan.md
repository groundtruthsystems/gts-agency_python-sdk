# Plan — Observability Tracing in the Agency SDK

Methodology: TDD per `workflow.md` (Red → Green → Refactor), >80% coverage,
mypy strict / black / bandit gates, and the Phase Completion Verification
Protocol at the end of every phase.

## Phase 1: Research & Design [checkpoint: 687e935]

- [x] Task: Consolidate research findings from `gts-demo-agent` (c48d604)
    - [x] Re-read `docs/observability.md` Part 1 & 2 and `demo/common/observability.py`, `demo/agency.py`
    - [x] Catalogue the 6 load-bearing mechanisms and mark which transfer verbatim vs adapt for the facade
- [x] Task: Author the SDK design document `docs/observability_design.md` (c48d604)
    - [x] Module layout (`agency_sdk/observability/`: `bootstrap.py`, `auth.py`, `__init__.py`) and public API surface
    - [x] Facade contract: `AgencyClient.observability(service_name, service_version=..., **opts)` signature, host/env resolution, instance caching
    - [x] Token-unification design: per-request hooks call `CredentialsSupplier.bearer_token()`; the demo's `KeycloakTokenProvider` is dropped
    - [x] Dependency strategy: `[observability]` extra contents + lazy-import / graceful-degradation rules
    - [x] Testing strategy: offline (requests+httpx stubbed), OTel in-memory exporters, what each phase tests
- [x] Task: Decide and document any `CredentialsSupplier` changes (c48d604)
    - [x] Assess need for an early-refresh buffer (avoid mid-flight expiry on the export path) and an `insecure`/verify option
    - [x] If changing the credentials contract, record the decision in `tech-stack.md` with a dated note (workflow rule 7)
- [x] Task: Conductor - User Manual Verification 'Phase 1: Research & Design' (Protocol in workflow.md) (687e935)

## Phase 2: Optional dependency & module scaffolding [checkpoint: 66ece16]

- [x] Task: Write failing tests for packaging & lazy-import behaviour (f91d761)
    - [x] `import agency_sdk` and an existing API client construct succeed with the extra NOT installed
    - [x] `AgencyClient.observability(...)` raises a clear, actionable error naming `[observability]` when deps are missing
- [x] Task: Implement the extra and package skeleton (Green) (f91d761)
    - [x] Add `[project.optional-dependencies] observability = [...]` to `pyproject.toml`; add `[observability]` to the dev test env
    - [x] Create `agency_sdk/observability/__init__.py` with the lazy-import guard and public exports
- [x] Task: Verify coverage, mypy strict, black for new files (f91d761)
- [x] Task: Conductor - User Manual Verification 'Phase 2: Optional dependency & module scaffolding' (Protocol in workflow.md) (66ece16)

## Phase 3: Auth layer & token unification (Mechanisms 2, 3, 4) [checkpoint: 0712118]

- [x] Task: Write failing tests for the auth hooks and header chain (3938586)
    - [x] `_RequestsBearerAuth` stamps a fresh `Authorization` from `CredentialsSupplier` on every `__call__`
    - [x] `_HttpxBearerAuth.auth_flow` mirrors it for httpx
    - [x] `build_headers` precedence: explicit OTEL header > bearer token > Langfuse Basic; `x-org-id` always set
    - [x] Endpoint resolution: explicit per-signal endpoint wins, else `host` + signal path
- [x] Task: Implement `auth.py` and the header/endpoint helpers (Green) (3938586)
- [x] Task: Implement agreed `CredentialsSupplier` changes (if any) with their own tests (3938586)
- [x] Task: Refactor; verify coverage / mypy / black (3938586)
- [x] Task: Conductor - User Manual Verification 'Phase 3: Auth layer & token unification' (Protocol in workflow.md) (0712118)

## Phase 4: Telemetry pipeline & log/trace correlation (Mechanisms 1, 6) [checkpoint: a496ffe]

- [x] Task: Write failing tests with OTel in-memory exporters (400d03c)
    - [x] `init()` builds a real recording `TracerProvider` + `LoggerProvider`; a started span has a valid (non-zero) span context
    - [x] A stdlib `logger.info(...)` inside an active span produces a log record carrying the span's trace_id/span_id
    - [x] Processor is selectable (Simple default, Batch option)
    - [x] Exporter-construction failure makes `init()` return `None` (graceful degradation), no exception
- [x] Task: Implement `bootstrap.py` telemetry pipeline (Green) (400d03c)
    - [x] Providers, OTLP span/log exporters wired through the auth hooks, processor choice
    - [x] `LoggingInstrumentor().instrument()` + root `LoggingHandler`
- [x] Task: Refactor; verify coverage / mypy / black (400d03c)
- [x] Task: Conductor - User Manual Verification 'Phase 4: Telemetry pipeline & log/trace correlation' (Protocol in workflow.md) (a496ffe)

## Phase 5: `agent_run` context manager & facade integration (Mechanism 5) [checkpoint: c4bf7b4]

- [x] Task: Write failing tests (908a6b1)
    - [x] `agent_run(name, **attrs)` opens a root span, sets attributes, and yields it
    - [x] When tracing is off (`init()` returned `None`), `agent_run` is a `nullcontext` and code still runs
    - [x] Child spans and logs opened inside `agent_run` share the root trace_id
    - [x] `AgencyClient.observability(...)` returns an `Observability` bound to the client's shared `CredentialsSupplier`; repeated calls return the same instance; OTLP host defaults to the client `base_url`, overridable
- [x] Task: Implement `agent_run` and `AgencyClient.observability()` (Green) (908a6b1)
- [x] Task: Refactor; verify coverage / mypy / black (908a6b1)
- [x] Task: Conductor - User Manual Verification 'Phase 5: agent_run & facade integration' (Protocol in workflow.md) (c4bf7b4)

## Phase 6: Langfuse client helper (FR8) [checkpoint: 362d253]

- [x] Task: Write failing tests (langfuse import stubbed) (aa0723f)
    - [x] `langfuse_client()` returns `None` when the package or keys are absent
    - [x] When present, the constructed client is authenticated via `_HttpxBearerAuth` and the shared span exporter
- [x] Task: Implement `langfuse_client()` with lazy import (Green) (aa0723f)
- [x] Task: Refactor; verify coverage / mypy / black (aa0723f)
- [x] Task: Conductor - User Manual Verification 'Phase 6: Langfuse client helper' (Protocol in workflow.md) (362d253)

## Phase 7: Example, docs & release polish [checkpoint: 99fe6e8]

- [x] Task: Add `examples/quick_observability.py` (3-line setup + `agent_run`, self-verifying where possible) (8987fa1)
- [x] Task: Documentation (8987fa1)
    - [x] Add SDK `docs/observability.md` (setup, preserved mechanisms, migration from the demo's inline bootstrap)
    - [x] Update `README.md`, `CLAUDE.md`; `conductor/product.md` + `conductor/tech-stack.md` handled by the doc-sync step (tech-stack credentials note already landed in Phase 1)
- [x] Task: Final gate run and version bump (8987fa1)
    - [x] `pytest --cov=agency_sdk --cov-report=term-missing` (>80%), `mypy agency_sdk/`, `black --check`, `bandit -r agency_sdk/ -x agency_sdk/test`
    - [x] Bump version in `pyproject.toml`
- [x] Task: Conductor - User Manual Verification 'Phase 7: Example, docs & release polish' (Protocol in workflow.md) (99fe6e8)

## Phase 8: Post-completion fixes [checkpoint: 0ff4c59]

- [x] Task: Fix double provider shutdown (atexit + explicit `shutdown()`) (cbba832)
    - [x] Write failing tests: `shutdown()` is idempotent (each provider's `shutdown` runs once across repeated calls + the atexit path); `init()` registers `self.shutdown` via atexit, not the raw provider `.shutdown` methods
    - [x] Implement: build providers with `shutdown_on_exit=False`; register `atexit.register(self.shutdown)`; null provider refs after shutdown so it is truly idempotent; correct the `shutdown()` docstring
    - [x] Verify coverage / mypy / black / bandit
- [x] Task: Conductor - User Manual Verification 'Phase 8: Post-completion fixes' (Protocol in workflow.md) (0ff4c59)

## Phase 9: Code-review follow-ups (PR #5) [checkpoint: 09d1342]

- [x] Task: H1 — make `AgencyClient.observability()` thread-safe (double-checked lock) (09d1342)
    - [x] Write failing test: concurrent calls construct exactly one `Observability` and all return the same instance
    - [x] Implement: add a `threading.Lock`; double-checked locking around the lazy build
- [x] Task: M2 — add session vault facade tests (cross-cutting; SDK repo) (09d1342)
    - [x] `client.session_vault()` returns `AgencySessionVaultClient` bound to `token_supplier`/`base_url`; repeated calls return the same instance
- [x] Task: Verify coverage / mypy / black / bandit (09d1342)
- [x] Task: Conductor - User Manual Verification 'Phase 9: Code-review follow-ups' (Protocol in workflow.md) (09d1342)

> M1 (handler.flush) and L2 (batch thread join) reviewed and judged non-issues. See PR #5 analysis.

## Phase 10: Code-review design/style follow-ups (PR #5) [checkpoint: e4a3cd3]

- [x] Task: L1 — make `Classification` a `StrEnum` (27d3ef2)
    - [x] Test: members equal their string values, `DEFAULT` is `RESTRICTED`, `str()` yields the bare value
    - [x] Implement: convert the plain class to `enum.StrEnum` (drop-in; members stay `str`)
- [x] Task: M3 — extract a shared `BaseDelegateClient` (`__init__` + `_request`/`_make_request`); ontology calls `_request(json_content_type=False)` for the raw `Response` (cbd84a5)
- [x] Task: M4 — introduce a `TelemetryConfig` dataclass grouping the OTLP/Langfuse/processor knobs (e4a3cd3)
- [x] Task: L3 — out of scope (deferred). Real diff is public param ordering across 6 other-track delegates + examples + downstream; breaking cross-track API change, not this track's remit (user decision)
- [x] Task: Verify coverage / mypy / black / bandit (e4a3cd3)
- [x] Task: Conductor - User Manual Verification 'Phase 10: Code-review design/style follow-ups' (Protocol in workflow.md) (e4a3cd3)
