# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Python client SDK for the GTS Agency platform. Provides typed HTTP clients for datasets, datasources, files, ontologies, prompts, and rules APIs.

- **Python:** >=3.12
- **Key deps:** requests, pydantic (v2), pyjwt
- **Optional deps:** `[observability]` extra — opentelemetry-sdk, otlp-http exporter, instrumentation-logging, langfuse (lazy-imported); `[openai]` extra — openai (lazy-imported, for the gateway full-feature helpers)

## Commands

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Install with dev + optional extras (observability/openai tests skip without theirs)
pip install -e ".[dev,observability,openai]"

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

**Entry point:** `AgencyClient` (in `client.py`) is a facade that composes seven delegate clients (plus the lazily built gateway and observability capabilities), all sharing a `CredentialsSupplier` for OAuth2 client-credentials auth with automatic token caching/refresh.

**Delegate pattern:** Each API domain has a client + DTO module pair in `delegates/`:
- `datasets_client.py` / `datasets_dto.py` — CRUD + filesystem traversal + clone
- `datasource_client.py` / `datasource_dto.py` — datasource + table introspection
- `files_client.py` / `files_dto.py` — tenant file storage: list/upload/folders/delete, signed URLs, `gtsf://` resolution, streamed download (see `docs/files_storage_flows.md`)
- `ontology_client.py` / `ontology_dto.py` — export (multiple formats) + entity-datasource mappings
- `prompts_client.py` + `domain.py` — prompt CRUD via command pattern (`POST /_command`)
- `rules_client.py` / `rules_dto.py` — rule listing, detail, execution + execution history
- `session_vault_client.py` / `session_vault_dto.py` — session-scoped key/value vault for agent state (classification-based encryption, audited reveal)
- `gateway_client.py` / `gateway_dto.py` — OpenAI-compatible LLM calls through the org's agentgateway, via `AgencyClient.gateway(*, org_id, gateway_base_url=None, environment=None)` (DCL-cached per `(org_id, gateway_base_url/environment)` identity; URL and environment are mutually exclusive). A **sibling** of `BaseDelegateClient`, not a subclass: own host (never `base_url`), fixed `/v1` path, 120s timeout, extra `x-org` header (not `x-org-id`). Tiered surface: zero-dep `complete()`/`complete_stream()` (native SSE; `stream=True` into the one-shot methods fails fast with `ValueError`), plus `[openai]`-extra `openai_client()`/`async_openai_client()` returning standard openai clients with rotating-bearer auth wired. DTOs are `extra="allow"` — the wire format is agentgateway upstream. Omitting `gateway_base_url` discovers the URL via `GET /api/agentgateways?o={org}` (Page-wrapped response; live-verified 2026-07-07). SSE parsing is byte-mode + explicit UTF-8 (text/event-stream has no charset; requests' ISO-8859-1 default corrupts multibyte chars). See `docs/gateway.md`, design in `docs/gateway_design.md`

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