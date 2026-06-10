# Product Definition — GTS Agency Python SDK

## Overview

The GTS Agency Python SDK is the official typed Python client for the GTS Agency
platform. It wraps the platform's REST APIs (datasets, datasources, ontologies,
prompts, rules) in a single, coherent, strongly-typed library so that any program
holding organisation credentials can interact with the platform without hand-writing
HTTP calls.

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
   resolution for the upcoming files client).

## Current Focus

**Track platform API coverage.** The platform evolves first; the SDK follows. The
single most urgent gap is the tenant file storage API
(groundtruthsystems/gts-agency_python-sdk#1): a files delegate client covering list,
upload, folder management, deletion, signed-URL retrieval, and `gtsf://<file_id>`
URI resolution.

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
