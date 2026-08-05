# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Python client SDK for the GTS Agency platform. Provides typed HTTP clients for datasets, datasources, files, ontologies, prompts, and rules APIs.

- **Python:** >=3.12
- **Key deps:** requests, pydantic (v2), pyjwt, openai (the gateway routes through the official openai SDK)
- **Optional deps:** `[observability]` extra — opentelemetry-sdk, otlp-http exporter, instrumentation-logging, langfuse (lazy-imported)

## Commands

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Install with dev + optional observability deps (observability tests skip without them)
pip install -e ".[dev,observability]"

# Type checking (strict mode)
mypy agency_sdk/

# Formatting (120 char line length)
black agency_sdk/

# Build package
python -m build

# Run an example
python examples/quick_clone_dataset.py

# Run tests (offline; requests is stubbed via monkeypatch)
pytest
```

## Architecture

**Entry point:** `AgencyClient` (in `client.py`) is a facade that composes ten delegate clients (plus the lazily built gateway and observability capabilities), all sharing a `CredentialsSupplier` for OAuth2 client-credentials auth with automatic token caching/refresh.

**Delegate pattern:** Each API domain has a client + DTO module pair in `delegates/`:
- `datasets_client.py` / `datasets_dto.py` — CRUD + filesystem traversal + clone
- `datasource_client.py` / `datasource_dto.py` — datasource + table introspection
- `files_client.py` / `files_dto.py` — tenant file storage: list/upload/folders/delete, signed URLs, `gtsf://` resolution, streamed download (see `docs/files_storage_flows.md`)
- `ontology_client.py` / `ontology_dto.py` — export (multiple formats) + entity-datasource mappings
- `prompts_client.py` + `domain.py` — prompt CRUD via command pattern (`POST /_command`)
- `rules_client.py` / `rules_dto.py` — rule listing, detail, execution + execution history
- `session_vault_client.py` / `session_vault_dto.py` — session-scoped key/value vault for agent state (classification-based encryption, audited reveal)
- `work_queue_client.py` / `work_queue_dto.py` — work-queue ingestion (`/api/work_queues`): create-item-with-external-refs, publish, `add_ref`, **queue-scoped owner lookup** (`get_items_by_ref` — a ref may be held once per queue; runs over the paginated `/items?ref_type=&ref_value=`, `_` = org scope), unblock/retry/reprocess commands, item delete, and `list(org)` (queue name→id). **409 is control flow, not an error**: `create_item`/`add_ref` catch the `HTTPError` and return typed claim-lost results — the owner summary comes from the standard error envelope's `error.details`, with an additive `contended` flag for the owner-less `CONFLICT_RETRY` fallback. Mirrors the gts-agency Track ① contract (guideline-agent `docs/dbq/files-inbox-ingestion-design-20260712.md` §5/§6)
- `session_client.py` / `session_dto.py` — report progress on a **dispatched** session (`/api/sessions/{id}/_command`): `attach(session_id)` binds the inherited session (no HTTP), `update(...)` posts the `{command:"update", organisation, update:{status,...}}` envelope. Exposes **only `attach`+`update`, never `register`** — the agent inherits the session id the ① worker injects, so self-registering would mint an orphan session. The SDK marshals a caller-decided `SessionStatus` (-1/0/2); it never infers the outcome. `AnalyticsEvent` is the promoted cross-agent event shape. Design: `docs/session_reporting_delegate_design.md`
- `annotations_client.py` / `annotations_dto.py` — publish a knowledge graph as annotator work (`/api/annotations` + `/api/annotation-specs`, via `AgencyClient.annotations()`). **Publishing is two calls, not one**: `create_batch` (`POST /_command`, standard `{success,message,data:{id}}` envelope) leaves the batch in DRAFT with 0 jobs; `upload_graph` (multipart `file`, 50 MiB cap) is what materialises **one job per vertex matching `target_class`** (default `rule`) and flips the batch to ACTIVE — and its response body is `null`, so the job count comes from a `get_batch` read-back. `push_graph` chains create → upload → read-back and is the agent-facing one-liner; the graph is the same `create.graph` payload sent to the ontology sandbox, accepted as a dict or a file path. Also `create_spec`/`get_spec`/`list_specs`, which seed the checklist a `job_type`'s jobs start from (**`get_spec`'s path segment is the `code`**, not the UUID). A failed upload leaves an empty DRAFT batch behind by design. See `docs/annotations.md`
- `session_templates_client.py` / `session_templates_dto.py` — read-only list of an org's session templates (`GET /api/session_templates`), via `AgencyClient.session_templates().list(org)`, so a caller can resolve a template **name → id** at runtime (the guideline-agent dispatcher wires queue/template by name from `schedule.static_input` instead of hardcoding ids)
- `gateway_client.py` / `gateway_dto.py` — LLM calls through the org's agentgateway, **openai-SDK-only**. `AgencyClient.gateway(*, org_id, gateway_base_url=None, environment=None)` (DCL-cached per `(org_id, gateway_base_url/environment)` identity; URL and environment are mutually exclusive) returns an `AgencyGatewayClient` that is a thin **factory**, not an HTTP client: `openai_client()`/`async_openai_client()` return standard `openai.OpenAI`/`AsyncOpenAI` wired to the gateway host (`/v1`), with the `x-org` routing header (not `x-org-id`) and a per-request rotating-bearer httpx auth hook (from `agency_sdk/auth_hooks.py`; the construction-time `api_key` is a placeholder). `openai` is a **core dependency**. `gateway_dto.py` holds only the discovery DTOs (`AgentGatewayStatusResponse`, `extra="allow"`); omitting `gateway_base_url` discovers the URL via `GET /api/agentgateways?o={org}` (Page-wrapped response; live-verified 2026-07-07). See `docs/gateway.md`, design in `docs/gateway_design.md`

**DTOs:** All models use Pydantic v2 `BaseModel`. Datasource, ontology, and rules DTOs use `ConfigDict(alias_generator=_to_camel, populate_by_name=True)` for camelCase JSON mapping. Prompt/dataset/files DTOs use snake_case matching the API.

**Shared type:** `Page` is defined in `datasets_dto.py` and imported by other DTO modules for pagination.

**Observability (optional):** `agency_sdk/observability/` (`bootstrap.py` + `auth.py`) adds OTLP tracing/logging to a Langfuse backend, reached via `AgencyClient.observability(...)`. Heavy OpenTelemetry/Langfuse imports are deferred to the lifecycle methods so importing the SDK never pulls them. The per-request bearer hooks (`auth.py`) and exporters reuse the shared `CredentialsSupplier`. Use `obs.init()` then `with obs.agent_run(name, **attrs):` (see `docs/observability.md`, design in `docs/observability_design.md`).

**Tests:** Offline suite in `agency_sdk/test/`; `conftest.py` stubs `requests` via monkeypatch so no test touches the network. mypy is relaxed for `agency_sdk.test.*` per pyproject overrides. End-to-end verification against the local stack: `docs/local_e2e.md`.

## Conventions

- Use `dict | None` syntax (PEP 604), not `Optional[Dict]` — Python 3.12+ only
- Pydantic v2 API: `model_dump(mode="json")`, `ConfigDict`, `Field`
- HTTP errors propagate via `response.raise_for_status()` — no custom exception wrapping
- API query params use abbreviations: `o` (org), `s` (size), `p` (page), `v` (version)
- Line length: 120 characters (black config)
- mypy strict mode for all production code; tests excluded

## CI/CD

`.github/workflows/publish.yaml` — triggered by `v*` tags. Builds, publishes to PyPI via trusted publisher (OIDC), notifies Slack.