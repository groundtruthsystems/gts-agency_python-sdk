# Tech Stack — GTS Agency Python SDK

## Language

- **Python ≥ 3.12** — required minimum; the codebase uses PEP 604 union syntax
  (`X | None`) exclusively. `Optional[X]` / `Dict[...]` style annotations are not
  used.

## Runtime Dependencies

| Dependency | Version | Role |
|---|---|---|
| `requests` | ≥ 2.32.0 | Synchronous HTTP client for all API calls |
| `pydantic` | ≥ 2.9.2 | DTO models, validation, JSON (de)serialisation |
| `pyjwt` | ≥ 2.3.0, < 3.0.0 | Decoding cached JWTs to check `exp` (no signature verification) |
| `openai` | ≥ 1.0.0 | Official openai SDK — the gateway routes exclusively through it (`gateway.openai_client()` / `async_openai_client()`); brings `httpx`. *Promoted from the `[openai]` extra to a core dep 2026-07-07 when the gateway's zero-dep client was removed.* |

Pydantic v2 API only: `model_dump(mode="json")`, `ConfigDict`, `Field`.

## Optional Dependencies

Installed via the `[observability]` extra
(`pip install gts-agency-python-sdk[observability]`); all imported lazily, so the
SDK core never requires them:

| Dependency | Version | Role |
|---|---|---|
| `opentelemetry-sdk` | ≥ 1.27.0 | Tracer/Logger providers + span/log processors |
| `opentelemetry-exporter-otlp-proto-http` | ≥ 1.27.0 | OTLP HTTP span/log exporters |
| `opentelemetry-instrumentation-logging` | ≥ 0.48b0 | Inject trace/span ids into stdlib log records |
| `langfuse` | ≥ 3.8.1 | Langfuse client (prompt mgmt/scoring); brings `httpx` |

## Development Tooling

| Tool | Version | Role |
|---|---|---|
| `mypy` | 1.4.1 | Type checking, **strict mode** for all production code; `agency_sdk.test.*` relaxed |
| `black` | 23.12.1 | Formatting, line length **120** |
| `pytest` | 7.4.0 | Test runner; offline suite in `agency_sdk/test/` stubs `requests` via monkeypatch |
| `pytest-cov` | 4.1.0 | Coverage measurement for the >80% workflow gate (added 2026-06-10, files_client track) |
| `pre-commit` | 3.8.0 | Git hook management |

## Build & Distribution

- **Build backend:** setuptools via `pyproject.toml` (`python -m build`).
- **Publishing:** GitHub Actions workflow (`.github/workflows/publish.yaml`)
  triggered by `v*` tags; publishes to PyPI through OIDC trusted publishing.
- **CI security gate:** `bandit -r agency_sdk/` runs as a required job before publish.

## Architecture

- **Pattern:** single-package client library. `AgencyClient` (`client.py`) is a
  facade composing per-domain delegate clients; each domain is a client + DTO module
  pair under `agency_sdk/delegates/`.
- **Observability (optional):** `agency_sdk/observability/` (`bootstrap.py` +
  `auth.py`) adds OTLP tracing/logging to a Langfuse backend via
  `AgencyClient.observability(...)`; OpenTelemetry/Langfuse imports are deferred to
  the lifecycle methods, and the per-request bearer hooks reuse `CredentialsSupplier`.
- **Agent gateway (core, 2026-07-07, openai-SDK-only):** `delegates/gateway_client.py`
  + `gateway_dto.py`. `AgencyClient.gateway(*, org_id, gateway_base_url=None,
  environment=None)` (DCL cache keyed by `(org_id, gateway_base_url/environment)` for
  correct multi-org routing; URL and environment are mutually exclusive) returns an
  `AgencyGatewayClient` that is a thin **factory**, not an HTTP client:
  `openai_client()`/`async_openai_client()` return standard `openai.OpenAI`/`AsyncOpenAI`
  wired to the gateway's own host (never the control-plane `base_url`) at the fixed
  `/v1` path, with the `x-org` routing header and a per-request rotating-bearer httpx
  auth hook (shared core module `agency_sdk/auth_hooks.py`; the construction-time
  `api_key` is a placeholder). *Simplified 2026-07-07:* the earlier zero-dependency
  built-in client (`complete`/`complete_stream`/`chat_completions[_stream]` + hand-rolled
  chat DTOs + native SSE parsing) was **removed** to unify on one path — `openai` owns
  the LLM surface (streaming, tools, structured outputs, retries, async) and was
  promoted to a core dependency. `gateway_dto.py` now holds only the discovery DTOs
  (`AgentGatewayStatusResponse`, `extra="allow"`). Omitting `gateway_base_url` resolves
  the URL from `GET /api/agentgateways?o={org}`; *live-verified 2026-07-07* against the
  control-plane image built 2026-07-06 (Page-wrapped response; discovery → completion
  chain passes).
- **Authentication:** shared `CredentialsSupplier` (`credentials.py`) implementing
  OAuth2 client-credentials with in-memory token caching and expiry-based refresh.
  - *Implemented (2026-06-17, observability track):* an early-refresh buffer
    (`refresh_buffer`, default 30 s) treats a token as expired shortly before its
    real `exp`. Rationale: the observability OTLP per-request auth hook
    re-reads this token on every export in a long-running process; refreshing at
    the exact `exp` risks stamping a token that expires in transit. Backward
    compatible — it only refreshes slightly sooner. An `insecure`/`verify=False`
    option was considered and **deferred** (token endpoint is plain HTTP locally;
    exporter-level TLS control, if needed, belongs in the observability module).
- **HTTP conventions:** every delegate owns a `_make_request` helper; errors
  propagate via `raise_for_status()`; 30 s default timeout; query parameter
  abbreviations `o` (org), `s` (size), `p` (page), `v` (version).
- **DTO conventions:** Pydantic v2 `BaseModel` throughout. Datasource/ontology/rules
  DTOs map camelCase JSON via `alias_generator=_to_camel`; dataset/prompt/files DTOs
  use snake_case matching the API. Shared `Page` pagination type lives in
  `datasets_dto.py`.
