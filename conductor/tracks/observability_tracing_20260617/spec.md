# Spec — Observability Tracing in the Agency SDK

## Overview

Downstream GTS agents (guideline-agent, document-enrichment-agent,
knowledge-researcher-agent, demo-agent, …) currently each wire OpenTelemetry
tracing/logging by hand. The reference implementation lives in
`gts-demo-agent` as a self-contained `OtelObservability` bootstrap
(`demo/common/observability.py`, documented in `docs/observability.md`).

This track folds that capability into the shared SDK so any agent holding an
`AgencyClient` can turn on OTLP tracing + log correlation (shipped to a Langfuse
backend) in a few lines, reusing the SDK's existing OAuth2 credentials. The
work is split into two phases: (1) a research-backed design that adapts the
demo's `OtelObservability` to the SDK's facade + `CredentialsSupplier`
architecture, and (2) a full, test-driven implementation of an
`agency_sdk/observability/` module exposed via `AgencyClient.observability()`.

The demo's six "counterintuitive but load-bearing" mechanisms (real recording
TracerProvider; per-request auth hooks; lazy/cached/early-refreshed token; auth
fallback chain; root-span context propagation; Simple-vs-Batch processor) are
the correctness backbone and must be preserved.

## Goals

1. Make tracing opt-in for any SDK consumer through `AgencyClient.observability()`
   with the same `CredentialsSupplier` already used for API calls — one cached
   token serves both the API client and the telemetry exporters.
2. Keep the SDK core lean: all heavy OpenTelemetry/Langfuse dependencies live
   behind an optional `[observability]` extra and are imported lazily.
3. Encode the error-prone boilerplate (root span, per-request auth) in the API
   so callers cannot get correlation wrong.

## Functional Requirements

### FR1 — `agency_sdk/observability/` module
- New package adapting the demo's `OtelObservability` into an `Observability`
  bootstrap. Suggested layout: `bootstrap.py` (the class), `auth.py`
  (per-request bearer hooks), `__init__.py` (public exports). An instrumentor
  registry is designed as an extension point but ships no framework
  instrumentors in this track (manual spans only).

### FR2 — Facade integration
- `AgencyClient.observability(service_name, service_version=..., **opts)`
  lazily builds and returns an `Observability` bound to the client's shared
  `CredentialsSupplier`. The OTLP/Langfuse host defaults to the client
  `base_url`, overridable via opts/env. Repeated calls return the same instance.

### FR3 — Unified token source (Mechanism 3)
- The per-request OTLP auth hook (`_RequestsBearerAuth`) and the Langfuse httpx
  hook (`_HttpxBearerAuth`) obtain their bearer token from the SDK's
  `CredentialsSupplier.bearer_token()`. The demo's separate
  `KeycloakTokenProvider` is not duplicated. If an early-refresh buffer or an
  `insecure` (verify=False) option is needed for the export path, it is added to
  `CredentialsSupplier` and recorded in `tech-stack.md` per the workflow.

### FR4 — Telemetry pipeline (Mechanisms 1 & 6)
- `init()` builds a real recording `TracerProvider` and a `LoggerProvider`, each
  with an OTLP HTTP exporter, and returns a tracer (or `None` if exporter setup
  fails — caller stays alive, untraced). The span/log processor is selectable
  (Simple default for parity/determinism; Batch for production).

### FR5 — Log/trace correlation
- `LoggingInstrumentor` stamps trace_id/span_id onto stdlib log records and a
  `LoggingHandler` exports them over OTLP, so logs join their span in Langfuse.

### FR6 — `agent_run()` context manager (Mechanism 5)
- A context manager (e.g. `with obs.agent_run("agent.run", **attrs) as span:`)
  opens the root span, sets business attributes, and degrades to a
  `nullcontext` when tracing is off — encoding the "open a root span first" rule
  so callers cannot orphan their logs/spans.

### FR7 — Per-request auth hooks (Mechanism 2)
- OTLP exporters get `session.auth = _RequestsBearerAuth(...)`; the Langfuse
  client gets `_HttpxBearerAuth`. A fresh token is stamped on every export; the
  static `Authorization` header is only a seed. The auth fallback chain
  (explicit OTEL header > bearer token > Langfuse Basic) stays configurable.

### FR8 — Langfuse client helper
- `langfuse_client()` returns a Langfuse client authenticated through the same
  bearer hook (for prompt management / scoring), or `None` when Langfuse keys /
  package are absent.

### FR9 — Optional dependency extra
- `pyproject.toml` gains `[project.optional-dependencies] observability = [...]`
  (opentelemetry-sdk, opentelemetry-exporter-otlp-proto-http,
  opentelemetry-instrumentation-logging, langfuse). The module imports these
  lazily; importing `agency_sdk` without the extra never fails, and
  `observability()` raises a clear, actionable error if the extra is missing.

### FR10 — Example & docs
- An `examples/quick_observability.py` showing the 3-line setup + `agent_run`,
  and `docs/observability.md` in the SDK describing setup, the preserved
  mechanisms, and migration from the demo's inline bootstrap.

## Non-Functional Requirements

- **Type safety:** `mypy agency_sdk/` strict passes (lazy/optional imports typed).
- **Style:** black, 120 cols; PEP 604 unions.
- **Tests:** offline suite in `agency_sdk/test/`; network stubbed (requests +
  httpx); OTel verified with in-memory exporters; >80% coverage on new code.
- **Security:** no committed secrets; `bandit -r agency_sdk/` clean.
- **Backward compatibility:** purely additive; existing `AgencyClient` usage and
  the lean core install are unaffected.

## Acceptance Criteria

1. With `[observability]` installed, `client.observability("gts-x").init()`
   returns a tracer and `with obs.agent_run("agent.x"):` produces, against a
   Langfuse backend, one trace whose nested spans and stdlib logs share a
   trace_id.
2. Without the extra installed, `import agency_sdk` and all existing API calls
   work unchanged; calling `observability()` raises a clear ImportError-style
   message naming the extra.
3. The OTLP exporters and Langfuse client authenticate with tokens minted by the
   shared `CredentialsSupplier`; a long-running process never sends an expired
   token (verified via the per-request hook test).
4. `init()` returning `None` (exporter failure) and `agent_run` as nullcontext
   keep the agent running untraced rather than crashing.
5. All quality gates pass: pytest, mypy strict, black --check, bandit, >80%
   coverage.

## Out of Scope

- Framework-specific instrumentors (LlamaIndex / Anthropic / Google GenAI) —
  the registry is an extension point only; no instrumentor is wired this track.
- Metrics (only traces + logs).
- Any CLI/TUI tooling.
- Migrating the downstream agents themselves onto the SDK module (follow-up).
- Changes to the Langfuse/Keycloak backend infrastructure (belongs to
  gts-local-environment).
