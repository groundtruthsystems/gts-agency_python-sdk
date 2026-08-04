# Spec — Agent Gateway Client (SDK side)

**Track:** `agent_gateway_client_20260706`
**Type:** Feature
**Branch:** `feat/agent-gateway-client`
**Design source:** `docs/gateway_design.md` (contract live-validated §10, 2026-07-06)
**Scope boundary:** SDK only (`gts-agency_python-sdk`) — Phase 1 of the design doc. guideline-agent
consumption (design Phase 2/3) is a separate track and out of scope here.

## Overview

Add a new opt-in capability accessor `client.gateway(...)` to `AgencyClient` that returns an
OpenAI-compatible LLM client (`AgencyGatewayClient`) pointed at the org's deployed **agentgateway**
Cloud Run service. It reuses the SDK's existing `CredentialsSupplier` (rotating Keycloak m2m JWT) as
the gateway `Authorization: Bearer` and stamps the gateway's required `x-org` routing header, exposing
`chat_completions(...)` (OpenAI-compatible primitive) and `complete(...)` (convenience returning
`choices[0].message.content`) that POST to `POST {gateway_url}/v1/chat/completions`.

The gateway client is a **core delegate** (stays on `requests`, no optional extra, no `require_*_deps`),
a **sibling** of `BaseDelegateClient` (different host, path, timeout, and the extra `x-org` header), so
it has zero blast radius on the seven existing delegates. The facade accessor mirrors the
`observability()` precedent: double-checked-locking cache, reuse of the shared `token_supplier`, but
targets the gateway's own Cloud Run host (never `base_url`).

URL resolution supports two paths: explicit `gateway_base_url` (primary), or — when omitted —
discovery via `GET /api/agentgateways?o={org}` on the control-plane `base_url`, selecting
`production.url` / `test.url` per an `environment` selector.

## Functional Requirements

### FR1 — `AgencyGatewayClient` (`agency_sdk/delegates/gateway_client.py`)
- Constructed with `token_supplier: CredentialsSupplier`, `gateway_base_url: str`, `org_id: str`.
- `_headers()` returns `Authorization: Bearer <token_supplier.bearer_token()>`,
  `Content-Type: application/json`, and `x-org: <org_id>` (lowercase header, decimal string value —
  NOT `x-org-id`, which is the observability OTLP header).
- `chat_completions(request: ChatCompletionRequest) -> ChatCompletionResponse` POSTs to
  `{gateway_base_url}/v1/chat/completions` with `json=request.model_dump(mode="json", by_alias=True,
  exclude_none=True)`, `timeout=120` (LLM calls are slow; base 30s is too tight), then
  `raise_for_status()` and parses the response.
- `complete(messages: list[dict], model: str, **kw) -> str` builds a `ChatCompletionRequest` and
  returns `choices[0].message.content or ""` (Qwen empty-content-on-length case returns `""`, §10).
- `gateway_base_url` is `.rstrip("/")`-normalized.

### FR2 — DTOs (`agency_sdk/delegates/gateway_dto.py`)
- Pydantic v2, plain snake_case (no alias generator; OpenAI wire is snake_case), `files_dto.py` style.
- `ChatMessage {role: str, content: str | None = None}`.
- `ChatCompletionRequest` (`extra="allow"`) with `model: str`, `messages: list[ChatMessage]`.
- `ChatChoice` (`extra="allow"`) with `index: int = 0`, `message: ChatMessage`.
- `ChatCompletionResponse` (`extra="allow"`) with `choices: list[ChatChoice]`.
- `extra="allow"` on request/choice/response is deliberate: the exact wire format is agentgateway
  v1.3.1 upstream, not owned by gts (§2.1, §8.9); unknown params/fields must pass through.

### FR3 — Facade accessor (`agency_sdk/client.py`)
- Add cache fields `self._gateway: AgencyGatewayClient | None = None` and
  `self._gateway_lock = threading.Lock()` in `__init__`.
- Add `gateway(*, org_id: str, gateway_base_url: str | None = None,
  environment: str = "production") -> AgencyGatewayClient`.
- Double-checked-locking build (modeled on `observability()` `client.py:82-89`); reuse
  `self.token_supplier`.
- When `gateway_base_url` is provided, use it; when omitted, call `_discover_gateway_url(org_id,
  environment)`.
- Lazy import of `AgencyGatewayClient` inside the method (matches observability's deferred import
  pattern).

### FR4 — URL discovery (`_discover_gateway_url`, verification-deferred)
- `_discover_gateway_url(self, org_id: str, environment: str) -> str` GETs
  `{self.base_url}/api/agentgateways?o={org_id}` with the shared control-plane bearer, parses a list of
  `AgentGatewayStatusResponse`, reads `items[0]`, and returns `production.url` or `test.url` per
  `environment`.
- Raises a clear `ValueError` when the selected slot / url is absent (e.g. gateway not enabled).
- DTOs `AgentGatewayStatusResponse` and `AgentGatewayEnvironmentResponse` (`extra="allow"`) modeled
  from control-plane `agent_gateway_dto.rs:24-47` (§4.1). Live-verification is deferred (local
  control-plane image predates the gateway feature); offline unit tests cover parsing + selection.

### FR5 — Offline tests (`agency_sdk/test/`)
- conftest stubs `requests` (no network). Assertions mirror `test_base_client.py` style.
- `chat_completions`/`complete`: request carries `x-org` header, targets the gateway host
  `/v1/chat/completions`, carries the Bearer token; response parsed to `choices[0].message.content`;
  empty-content returns `""`; `raise_for_status` propagation on 401/403 (plain-text error bodies, §10).
- `gateway()` facade: DCL returns a cached instance; explicit `gateway_base_url` path; discovery path.
- `_discover_gateway_url`: hits `/api/agentgateways?o={org}`, selects `production.url` vs `test.url` by
  `environment`, raises on missing url.

### FR6 — Docs & example
- `docs/gateway.md` — usage guide (accessor, prod/test URL selection, `x-org`, discovery, example).
- `examples/quick_gateway.py` — env-driven, self-verifying, runnable against the local gateway on
  `:4000` for E2E.
- Update `README.md` "Delegate Clients" section and `CLAUDE.md` Architecture to list the gateway
  delegate. Keep `docs/gateway_design.md` as the design reference.

## Non-Functional Requirements

- **Python ≥ 3.12**, PEP 604 unions (`X | None`), no `Optional[...]`.
- **mypy strict** passes for `agency_sdk/` (tests relaxed per pyproject overrides).
- **black** 120-char line length; **bandit** clean (`-x agency_sdk/test`).
- **Coverage > 80%** for new code (workflow gate).
- No new runtime dependencies; no `[gateway]` optional extra; stays on core `requests`.
- Errors propagate via `raise_for_status()` — no custom exception wrapping; must NOT assume a JSON
  error body (gateway returns plain-text 401/403, §10).
- `pyproject.toml` version is **not** bumped in this track; rc11 bump + tag + PyPI publish is a
  separate post-merge step.

## Acceptance Criteria

- `AgencyClient(...).gateway(org_id="2", gateway_base_url="http://localhost:4000")` returns an
  `AgencyGatewayClient`; `.complete([...], model="biglambda1")` against the local `:4000` gateway
  returns non-empty assistant text with a valid Bearer + `x-org: 2`.
- Wrong / missing `x-org` → HTTP 403; missing Bearer → HTTP 401; both surface via `raise_for_status()`.
- `gateway(org_id="2", environment="test")` (no `gateway_base_url`) issues one
  `GET /api/agentgateways?o=2` and targets `test.url` (verified offline).
- `pytest` green; `mypy agency_sdk/` clean; `black --check` clean; coverage > 80% on new modules.
- `examples/quick_gateway.py` exits 0 with each lifecycle step printed PASS against local `:4000`.

## Out of Scope

- guideline-agent consumption: `GatewayLLMClient`, `GatewayProviderConfig`/`_PROVIDER_CLASSES`,
  `dependencies.py` dispatch branch, `config.json` profile routing (design Phase 2/3, separate repo).
- Streaming (`stream=true` / SSE) — future seam (§8.6).
- Client-side retry/backoff on 429/5xx — SDK keeps its no-retry convention; retry posture lives in the
  agent's async client (§8.6). SDK gateway client uses plain `raise_for_status`.
- `pyproject.toml` version bump, tagging, and PyPI release (post-merge, §10 decision 2).
- Any `gts-local-environment` change (e.g. re-pulling a newer control-plane image to live-verify
  discovery) — separate repo, separate task.
- Live verification of the `/api/agentgateways` discovery endpoint (deferred; offline-tested only).
