# Product Definition — GTS Agency Python SDK

## Overview

The GTS Agency Python SDK is the official typed Python client for the GTS Agency
platform. It wraps the platform's REST APIs (datasets, datasources, files,
ontologies, prompts, rules, session vault) in a single, coherent, strongly-typed
library so that any program holding organisation credentials can interact with the
platform without hand-writing HTTP calls — and routes agent LLM traffic through the
org's agent gateway with the same single credential.

## Target Users

1. **Internal GTS agent and service developers** — downstream programs such as
   guideline-agent, document-enrichment-agent, and knowledge-researcher-agent that
   consume platform data and rules programmatically.
2. **External customer engineers / third-party integrators** — teams building on the
   GTS Agency platform who need a supported, stable integration path.
3. **Data scientists and analysts** — script-style consumers querying platform data,
   executing rules, and exporting ontologies from notebooks or ad-hoc scripts.

## Value Proposition

Compared to calling the REST APIs directly, the SDK provides:

1. **Type safety and developer experience** — every request and response is a
   Pydantic v2 model; IDE completion and mypy strict guarantees eliminate JSON
   structure guesswork.
2. **Zero-effort authentication** — OAuth2 client-credentials flow, JWT caching, and
   automatic refresh are built into a shared `CredentialsSupplier`; consumers never
   touch tokens.
3. **High-level workflow encapsulation** — multi-step API orchestrations are exposed
   as single methods (e.g. `clone_dataset` recursive download; `gtsf://` URI
   resolution and streamed `download()` in the files client).
4. **Optional observability** — opt-in OpenTelemetry tracing and stdlib-log
   correlation, shipped to a Langfuse backend via `client.observability(...)`,
   reusing the SDK's `CredentialsSupplier` so one cached token serves both API
   calls and telemetry. Heavy dependencies stay behind the `[observability]` extra.
5. **LLM gateway routing** — `client.gateway(...)` returns an OpenAI-compatible
   `openai` client for the org's deployed agentgateway: agents send LLM traffic
   with the same rotating m2m JWT (plus the `x-org` routing header) instead of
   holding per-provider API keys; provider secrets stay in the org's gateway
   config. The SDK wires auth / `x-org` / URL into a standard official `openai`
   client (`gateway.openai_client()` / `async_openai_client()`) and hands it
   back, so the full OpenAI surface (streaming, tools, structured outputs,
   retries, async) works as documented. `openai` is a core dependency.

## Current Focus

**Track platform API coverage.** The platform evolves first; the SDK follows. The
tenant file storage client (groundtruthsystems/gts-agency_python-sdk#1) shipped on
2026-06-11 with offline protocol tests and static gates; its real end-to-end
verification against the gts-local-environment stack is the current focus, followed
by continued coverage of new platform APIs.

Optional OpenTelemetry tracing/logging support (the `[observability]` extra and
`AgencyClient.observability(...)`) shipped on 2026-06-17.

The agent gateway client (`AgencyClient.gateway(...)`, OpenAI-compatible chat
completions with `x-org` routing and optional control-plane URL discovery)
shipped on 2026-07-07, live-validated against the local agentgateway.

The gateway was then unified on the official `openai` SDK (2026-07-07,
CTO-driven): the zero-dependency built-in client was removed, `openai` promoted
to a core dependency, and `AgencyGatewayClient` reduced to a factory returning
pre-wired `openai` clients — one usage path, all live-validated.

## Non-Goals

- **No CLI / TUI.** This is a pure library. Command-line tooling, if ever needed,
  belongs in a separate package built on top of the SDK.

## Success Criteria

1. **API coverage parity** — every stable API domain the platform exposes has a
   corresponding delegate client in the SDK.
2. **Zero-friction onboarding** — `pip install` plus a few lines of code completes a
   first successful call; the bundled example scripts run out of the box against a
   configured environment.
3. **Downstream adoption** — internal GTS agents and external integrators use the SDK
   as the standard integration path instead of maintaining bespoke HTTP clients.
