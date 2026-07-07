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

Installed via the `[openai]` extra (lazy-imported; the core never requires it):

| Dependency | Version | Role |
|---|---|---|
| `openai` | ≥ 1.0.0 | Official openai SDK returned by the gateway full-feature helpers `openai_client()`/`async_openai_client()` |

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
- **Agent gateway (core, 2026-07-07):** `delegates/gateway_client.py` +
  `gateway_dto.py` provide OpenAI-compatible chat completions through the org's
  agentgateway via `AgencyClient.gateway(*, org_id, gateway_base_url=None,
  environment="production")` (DCL cache keyed by `(org_id, gateway_base_url/environment)`
  — changed 2026-07-07 from observability-style single-instance, which silently served
  the first caller's org/host to every later caller; gateway clients are stateless, so
  per-identity instances are free and multi-org processes get correct routing). The client is
  a deliberate **sibling** of `BaseDelegateClient`, not a subclass: it targets the
  gateway's own host (never the control-plane `base_url`), uses the fixed `/v1`
  path, a 120 s timeout, and stamps the extra `x-org` header. DTOs are
  `extra="allow"` — the wire format is agentgateway upstream, not owned by gts.
  *Extended 2026-07-07 (streaming/openai track):* native SSE streaming
  (`chat_completions_stream`/`complete_stream`; byte-mode `iter_lines(delimiter=b"\n")`
  + explicit per-line UTF-8 decode, because text/event-stream carries no charset and
  requests defaults `text/*` to ISO-8859-1, which corrupts multibyte chars and lets a
  0x85 byte split lines; `stream=True` into the one-shot methods fails fast with
  `ValueError`), plus `[openai]`-extra helpers `openai_client()`/`async_openai_client()`
  returning standard openai clients with the rotating bearer (per-request httpx auth
  hook from the shared core module `agency_sdk/auth_hooks.py`) and `x-org` pre-wired — the
  full-feature tier (tools, structured outputs, retries, async).
  Omitting `gateway_base_url` resolves the URL from `GET /api/agentgateways?o={org}`.
  *Live-verified 2026-07-07* against the control-plane image built 2026-07-06: the
  real response is Page-wrapped (`{"page": ..., "items": [...]}`, matching the SDK's
  standard pagination, not the bare list modeled from the Rust source), and the full
  discovery → completion chain passes; the client handles both shapes. No new
  runtime dependencies.
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
