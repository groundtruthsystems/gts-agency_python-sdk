# Agent Gateway (OpenAI-compatible LLM) support in the gts-agency Python SDK

> Revised: 2026-07-06

Design doc for adding **agent gateway** routing to the `gts-agency_python-sdk`, so that any
SDK-consuming agent (starting with `gts-guideline-agent`) can send its LLM traffic through the
per-org **agentgateway** instead of holding per-provider API keys. The gateway has separate
**PRODUCTION** and **TEST** environments.

Every non-obvious claim below is grounded in code with a `file:line` citation. Where a fact is
not fixed by the code we own (e.g. the exact OpenAI wire params), it is called out explicitly and
moved to **Open questions / decisions**.

---

## 1. Summary

### What we are adding

A new capability on the SDK's `AgencyClient` — `client.gateway(...)` — that returns an
OpenAI-compatible LLM client pointed at the org's deployed **agentgateway** Cloud Run service. It:

- reuses the SDK's existing `CredentialsSupplier` (rotating Keycloak m2m JWT) as the gateway
  `Authorization: Bearer`, and
- stamps the gateway's required `x-org` routing header,
- exposing a `chat_completions(...)` / `complete(...)` surface that POSTs to
  `POST {gateway_url}/v1/chat/completions`.

Then `gts-guideline-agent` adopts it as a new `gateway` LLM provider that satisfies the existing
`LLMClient` protocol (`guideline_agent/llm/clients.py:8-22`), routed via the existing
`llm_task_profiles` config with zero changes to the pipeline stages or the tracing wrapper.

### Why

- **Centralize provider credentials at the gateway.** Today each guideline-agent LLM client holds
  a static per-provider key: llama.cpp `Authorization: Bearer <api_key>`
  (`guideline_agent/llm/llamacpp_client.py`), Claude `x-api-key`
  (`guideline_agent/llm/claude_client.py:59-63`), Gemini `x-goog-api-key`
  (`guideline_agent/llm/gemini_client.py:90`). These come from `${ENV_VAR}` placeholders resolved
  at config load. Routing through the gateway lets the org's gateway config hold the upstream
  provider secret (`llm.providers[].params.apiKey`, rendered by the control plane at
  `gts-agency-control/src/service/agent_gateway_service/config.rs:129-181`) and the agent holds
  **one** m2m credential.
- **One org m2m credential.** The agent already mints a rotating Keycloak JWT via
  `CredentialsSupplier` for prompts + observability (`guideline_agent/control_plane.py:16-38`).
  The gateway accepts that same JWT, so the agent no longer needs separate provider keys.
- **Kill committed provider keys.** Once traffic is routed, the `openai`/`gemini`/`claude` keys can
  be dropped from the agent's config/secrets (see §7).
- **One routing + observability point.** The gateway is the single egress for all LLM calls per org;
  routing (which upstream model serves a request) becomes a control-plane config change, not an
  agent redeploy.
- **Remove ModelRegistry multi-provider drift.** Today `guideline_agent/config.py:78-82` hardcodes
  three provider classes and `dependencies.py:94-99` hardcodes three `isinstance` branches. Routing
  through one `gateway` provider collapses provider selection into the gateway's virtual-model map,
  reducing the agent to a single client type over time.

---

## 2. How the agent gateway works today

### 2.1 What is deployed

The "agent gateway" is the open-source Linux Foundation **`agentgateway` binary** (image
`…/gts-external/agentgateway:v1.3.1`, `gts-agency-common/src/config/config.rs:116-134`), deployed
**per org** as a Cloud Run service. The gts-agency Rust repo is a **control plane**: it renders the
gateway's config YAML and provisions/updates the Cloud Run service + Secret Manager secret. It does
**not** implement `/v1/chat/completions` — that endpoint, the OpenAI-compat request/response
parsing, the `virtualModels` routing, JWT enforcement, and the `x-org` authz check are all executed
by the deployed `agentgateway` binary, driven by the `llm:` config the control plane writes.

The container listens on **port 4000** for LLM traffic (Cloud Run ingress) and exposes its admin UI
on 15000 (`gts-agency-control/src/service/infrastructure/gcp_agentgateway_runtime.rs:352-379`). The
public `run.app` URL routes to :4000, the OpenAI-compatible listener.

> **Caveat carried forward from verification.** The exact set of OpenAI params that pass through and
> the exact response JSON shape are defined by agentgateway v1.3.1 upstream, **not** by any struct in
> the gts repos. This repo authoritatively fixes only the auth/authz policy, the routing config, the
> URL naming, the prod/test lifecycle, and the discovery API. Treat unknown request/response fields
> as "OpenAI Chat Completions, proxied" — see §8.

### 2.2 The `/v1/chat/completions` call contract

#### Auth: Keycloak JWT (JWKS / issuer)

The rendered config template (`gts-agency-control/src/service/agentgateway/template.rs:10-30`,
verbatim in the `TEMPLATE` const) enforces, under `llm.policies.jwtAuth`:

```yaml
    jwtAuth:
      mode: strict
      issuer: {{ issuer }}
      audiences:
{{ audiences_block }}
      jwks:
        url: {{ jwks_url }}
      jwtValidationOptions:
        requiredClaims:
        - exp
        - aud
        - sub
        - iss
```

Resolution (`gts-agency-control/src/service/agent_gateway_service/config.rs:12-32`):

- `issuer` = `{base}/realms/{realm}` (`config.rs:29`), e.g. `https://auth.example.com/realms/agency`.
- `jwks.url` = `{issuer}/protocol/openid-connect/certs` (`config.rs:30`).
- `audiences` = **hardcoded `["account"]`** (`config.rs:31`) — Keycloak's default per-client audience.
- `mode: strict` → any request without a valid token is rejected.
- Required claims: `exp`, `aud`, `sub`, `iss` must all be present.

This is the **same Keycloak realm** that guards the control-plane REST API, so the token minted by
`CredentialsSupplier` for prompts/observability is directly reusable as the gateway Bearer. The
gateway validates it independently via its own rendered `jwtAuth` block.

> The `account` audience is Keycloak's default. The token `CredentialsSupplier` mints must carry
> `account` in `aud` — flagged in §8 as a verify-before-ship item.

#### Authz: the `x-org` header

Org scoping is a single authorization rule keyed on the **`x-org` request header**, rendered with
the org's numeric id baked in (`template.rs:14-16`):

```yaml
    authorization:
      rules:
      - allow: '"x-org" in request.headers && request.headers["x-org"] == "{{ org_id }}"'
```

The test `renders_org_issuer_and_jwks` (`template.rs:76-94`) confirms org 42 renders literally
`request.headers["x-org"] == "42"`. So the client **must** send:

- header name **`x-org`** (lowercase; note this is *not* `x-org-id`, which is the observability OTLP
  header — `agency_sdk/observability/bootstrap.py:153`),
- value = the org id **as a decimal string** (it is a string comparison).

Org isolation comes **entirely** from this header. The JWT is validated for authenticity but its
claims are **not** checked against the org — a valid `account`-audience realm token plus the correct
`x-org` header is sufficient.

> **Adversarial verification flagged this as a mutable default, not an invariant.** The template
> above is the *seed* used on first `enable`. The `save` command
> (`gts-agency-control/src/service/agent_gateway_service/commands.rs:227-280`) accepts an arbitrary
> caller-supplied config and stores it verbatim; `build_effective` (`config.rs:54-89`) regenerates
> only `llm.models`/`llm.providers`, **not** `llm.policies`. A principal with write access could
> publish a config that weakens/removes the `x-org` or `jwtAuth` blocks. For our purposes we build
> the client to the **default** contract (send both JWT + `x-org`); do not rely on the gateway as the
> sole auth boundary in threat modeling.

#### Body / model / messages

OpenAI Chat Completions shape: `{ "model": "<virtual-model>", "messages": [...], ...OpenAI params }`.
The **only** field this repo constrains is `model`, which must resolve to a rendered virtual-model
entry (§2.3). Other OpenAI params pass through to the upstream provider per agentgateway's proxy
behavior.

#### Response

OpenAI Chat Completions response, produced by agentgateway proxying the mapped backend. Extract the
assistant text at `choices[0].message.content` (standard OpenAI shape; not modeled in the gts repos).

#### Concrete example (token redacted)

```bash
curl -sS -X POST \
  "https://agentgateway-org-2-wadexavawa-uk.a.run.app/v1/chat/completions" \
  -H "Authorization: Bearer <KEYCLOAK_JWT_REDACTED>" \
  -H "x-org: 2" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "biglambda1",
    "messages": [
      {"role": "system", "content": "You are a clinical extraction assistant."},
      {"role": "user", "content": "Summarize this rule ..."}
    ],
    "temperature": 0.0
  }'
```

Corroborated by the live local config
(`gts-local-environment/configurations/agentgateway/config.yaml`), whose policies match the template:
`x-org == "2"`, `jwtAuth mode: strict`, `issuer http://localhost:8080/realms/agency`, JWKS at
`.../realms/agency/protocol/openid-connect/certs`, required claims `exp, aud, sub, iss`; upstream
`baseUrl: https://chat.biglambda.groundtruthsystems.com`.

### 2.3 Virtual models and the default provider

The rendered config has `llm.virtualModels` (template default `[]`) plus `llm.models` and
`llm.providers`, regenerated from scratch on every save/publish
(`config.rs:build_effective` 54-89; `build_llm_entries` 115-234).

- **Providers** (`llm.providers`, `config.rs:129-181`): one entry per assigned `ModelProvider`,
  by type: `openai`, `bedrock`, `vertex`, `gemini`, `anthropic` (others skipped; gated at
  create/update, `model_provider_service.rs:522-527`). Upstream secrets live here.
- **Models** (`llm.models`, `config.rs:191-230`) — the virtual-model map. The client's `"model"`
  string is matched literally against `llm.models[].name`:
  - **Explicit models**: each declared `{name, ...}` becomes a virtual model routed to
    `provider.reference`, optionally rewriting the upstream id (`target_model`) or stripping a prefix
    (`strip_prefix`). Names are unique per gateway
    (`model_provider_service.rs:validate_unique_model_names`, `:93-144`).
  - **Default single-model mode** (`config.rs:222-229`): a provider marked `is_default` (the starred
    "Default" in the UI, set via `set_default`, `model_provider_service.rs:431-461`) renders as the
    `"*"` catch-all — **any** `model` string routes to it. A sole assigned provider with no explicit
    default also becomes `"*"`. Otherwise the virtual-model name is the provider's own `name`.

**Implication for the client:** the valid `model` values are whatever the org's gateway config
renders. The agent must be told the concrete virtual-model name per task (e.g. `biglambda1`) via
config — there is no automatic `<providerType>/<model>` dispatch. If the org has a `"*"` default
provider, any model string routes through, which is the simplest starting posture.

---

## 3. Production vs Test environments (first-class requirement)

### 3.1 Data model

Each org has **two** `agent_gateway_deployment` rows: `environment = "production"` and
`environment = "test"` (`gts-agency-control/src/model/agent_gateway_deployment.rs:31-32`), each with
its own `url`, `status`, `version` (`:43`, `:47`). Production-only columns: `code` (org-unique
identity, e.g. `gateway-1a2b3c4d`) and `working_config`.

Config is versioned in `agent_gateway_config_version` rows with `state ∈ {draft, published,
archived}` and a monotonic `version` int:

- **Test runs the latest `draft`**; **production runs the `published`** version; superseded published
  versions become `archived` (retained for rollback).

### 3.2 Distinct, persistent Cloud Run URLs

**Verified: prod and test are two separate Cloud Run services with two separate names → two distinct,
persistent URLs. This is NOT blue/green on one URL.** The only difference the gts code introduces is a
`-test` infix on the service id
(`gts-agency-control/src/service/infrastructure/gcp_agentgateway_runtime.rs:138-144`):

```rust
fn service_id(org_id: i64, environment: &str) -> String {
    if environment == ENV_TEST {
        format!("agentgateway-org-{}-test", org_id)
    } else {
        format!("agentgateway-org-{}", org_id)
    }
}
```

For org 2: production service `agentgateway-org-2`, test service `agentgateway-org-2-test`. GCP
assigns the `-{hash}-{region}.a.run.app` suffix. The URL is **read back** from the deployed service
(`gcp_agentgateway_runtime.rs:458-466`, `let url = service.uri;`) and persisted onto the row. So:

- prod: `https://agentgateway-org-2-wadexavawa-uk.a.run.app`
- test: `https://agentgateway-org-2-test-wadexavawa-uk.a.run.app`

Test also uses fixed scaling min 0 / max 1 (`gcp_agentgateway_runtime.rs:398-403`) vs production's
configured bounds — a revision-scaling nuance, not a URL split.

### 3.3 Lifecycle (save-to-test / publish / rollback / discard)

All lifecycle actions go through `POST /api/agentgateways/_command`
(`gts-agency-control/src/service/agent_gateway_service/commands.rs:72-90`):

| UI action | `command` | Effect |
|---|---|---|
| Enable | `enable` | Provisions both prod + test services, version 1 = published, deploys to both. `commands.rs:147-225` |
| **Save to test** | `save` | Rebuilds effective config, upserts the `draft`, deploys **draft → test only**. `commands.rs:227-280` |
| **Publish to production** | `publish` | Archives current published, flips draft → published, deploys **→ production**. `commands.rs:282-312` |
| **Roll back** (prod) | `rollback_production` | Flips previous published back, redeploys **→ production**. `commands.rs:341-372` |
| **Discard draft** | `rollback_draft` | Deletes the draft, redeploys current published **→ test** (test reverts to prod). `commands.rs:314-339` |
| Disable | `disable` | Tears down both services + secrets. `commands.rs:374-402` |

So **the test URL always serves the latest draft** (or published after a discard); **the production
URL serves the published version**. URLs are stable across versions (same service, new revision).

### 3.4 How a client selects prod vs test

The client picks **which of the two URLs** to call. There is no per-request env switch — you point the
gateway client at the production URL or the test URL. In the SDK this is an
`environment: "production" | "test"` selector (§5) that chooses which discovered/configured URL to
use. Recommended deployment mapping (see §7): staging agents → **test** URL; production agents →
**production** URL.

---

## 4. Gateway URL discovery

**Question: can the SDK discover the prod/test URL via the control-plane API, or must it be
configured?**

**Answer: it CAN be discovered, and discovery is well-supported — but for v1 we recommend
configure-with-optional-discovery-fallback.**

### 4.1 Discovery is available

`GET /api/agentgateways?o={org}` (routes at `gts-agency-control/src/handler/agent_gateway.rs:63-70`,
mounted at root, `main.rs:334-335`) returns one `AgentGatewayStatusResponse` per org with **both**
URLs, labeled (`gts-agency-control/src/service/agentgateway/agent_gateway_dto.rs:24-47`):

```rust
pub struct AgentGatewayStatusResponse {
    pub enabled: bool,
    pub code: Option<String>,
    pub production: Option<AgentGatewayEnvironmentResponse>,  // prod slot
    pub test: Option<AgentGatewayEnvironmentResponse>,        // test slot
    ...
}
pub struct AgentGatewayEnvironmentResponse {
    pub environment: String,      // "production" | "test"
    pub status: String,           // provisioning | ready | failed | disabled
    pub url: Option<String>,      // <-- the run.app URL  (queries.rs:118, from dep.url)
    pub version: Option<i32>,
    ...
}
```

So a discovering client reads `items[0].production.url` / `items[0].test.url` (list) or the same off
the single-get. Requires a control-plane Keycloak bearer + READ on `agent_gateways`
(`queries.rs:_check_read`). `/api/model-providers` returns **no URL** — it is the upstream connection
config, not a callable endpoint (`model_provider_dto.rs:35-51`); do not use it for URL resolution.

### 4.2 Recommendation

- **v1 — explicit config (recommended default).** Pass `gateway_base_url` (per environment) via the
  agent's config. It is offline-testable, has no startup network round-trip, and mirrors how the
  agent already reads a `control_plane` block. **This is the primary path.**
- **Fallback / convenience — discovery.** When `gateway_base_url` is omitted, the SDK's
  `client.gateway(environment=...)` MAY resolve the URL by calling
  `GET /api/agentgateways?o={org}` on the control-plane `base_url` and reading
  `production.url` / `test.url`. This adds one round-trip and a dependency on the gateway being
  `enable`d, so it is opt-in, not the default.

Rationale: discovery is real and clean, but a design that boots without a live control-plane call is
strictly more robust for a batch extraction agent. Ship explicit-config first; layer discovery as the
default-when-omitted path in a later rc.

---

## 5. SDK design

### 5.1 Accessor: `client.gateway(...)`

Add a **new capability accessor** on `AgencyClient`, named **`gateway(...)`** (not `llm(...)` — the
returned object is the org's routing gateway, and "gateway" matches the control-plane vocabulary
`AgentGatewayStatusResponse` and the product UI). It returns an `AgencyGatewayClient`.

Model it on the **`observability()` precedent** (`agency_sdk/client.py:56-104`), which is the
established template for "opt-in capability that reuses the shared credentials":

- reuse `self.token_supplier` (`client.py:92`) — one cached/rotating token,
- double-checked-locking cache (`client.py:82-89`, fields `:32-33`),
- **diverge on host**: observability defaults `host=host or self.base_url` (`client.py:96`) because
  Langfuse can sit behind the same ingress; the gateway is a **different** Cloud Run host, so it takes
  an explicit `gateway_base_url` (or resolves it via discovery, §4), never `base_url`.
- **No optional-extra guard.** observability needs `require_observability_deps()`
  (`observability/__init__.py:37-48`) because it pulls heavy deps. The gateway client stays on core
  `requests` (already an SDK dependency), so **no `[gateway]` extra, no `require_*_deps()`** — it is a
  core delegate like `rules()`/`files()`.

### 5.2 Targeting the host + injecting `x-org`

`AgencyGatewayClient` takes `gateway_base_url` + `org_id`, reuses `token_supplier` for the Bearer, and
stamps `x-org` per request. Note `BaseDelegateClient` sends **only** `Authorization` + `Content-Type`
(`agency_sdk/delegates/base_client.py:40-42`) and has **no** headers hook, and the gateway needs a
different host, path, and timeout — so the gateway client is a **sibling** of `BaseDelegateClient`,
not a subclass. This keeps zero blast radius on the seven existing delegates.

### 5.3 Method surface

Provide both:

- `chat_completions(request) -> ChatCompletionResponse` — the OpenAI-compatible primitive.
- `complete(messages, model, **kw) -> str` — a thin convenience that builds the request and returns
  `choices[0].message.content`. This is the shape guideline-agent's `LLMClient` protocol wants
  (`guideline_agent/llm/clients.py:18-22`).

### 5.4 SYNC vs ASYNC

**Verified facts:** the SDK is 100% synchronous `requests` end-to-end
(`base_client.py:43`, `credentials.py:37`); the only `httpx` in the SDK is the observability OTLP
exporter, and it is a **sync** `httpx.Client`. Meanwhile guideline-agent's `LLMClient.complete` is
`async` (`clients.py:18-22`), and its own Claude/Gemini clients are already
`httpx.AsyncClient`-based (`claude_client.py:24`, `gemini_client.py:24`).

**Recommendation: ship a SYNC gateway client in the SDK** (`requests`, matching the SDK's single-HTTP-
stack invariant), and let async consumers bridge. There are two clean bridges, and this doc recommends
the **second** for guideline-agent specifically:

1. **SDK sync client + `asyncio.to_thread` in the async consumer.** `await
   asyncio.to_thread(client.gateway(...).complete, messages, model)`. Keeps one HTTP stack in the SDK.
2. **Agent builds its own async `httpx` client, reusing only `CredentialsSupplier`.** Because
   guideline-agent is *already* an `httpx.AsyncClient` shop, the lowest-friction agent-side path is a
   native-async `GatewayLLMClient` that calls `creds.bearer_token()` for the Bearer and adds `x-org`
   — see §6. It does not need the SDK's sync delegate at all; it needs the SDK's `CredentialsSupplier`
   (which it already imports via `control_plane.py:37`).

**Do not** migrate the whole SDK to httpx or add a second async stack to the SDK for one delegate.
Leave native-async/streaming as a documented future seam in the SDK; the agent gets async today via
option 2.

### 5.5 Concrete sketch

New module `agency_sdk/delegates/gateway_client.py`:

```python
import requests
from agency_sdk.credentials import CredentialsSupplier
from agency_sdk.delegates.gateway_dto import ChatCompletionRequest, ChatCompletionResponse


class AgencyGatewayClient:
    """OpenAI-compatible client for the org's deployed agentgateway.

    Reuses the shared CredentialsSupplier (rotating Keycloak m2m JWT) as the
    gateway Bearer and stamps the ``x-org`` routing header. Targets the gateway's
    own Cloud Run host, not the control-plane base_url.
    """

    api_path = "/v1"  # OpenAI-compatible path on the gateway host

    def __init__(self, token_supplier: CredentialsSupplier, gateway_base_url: str, org_id: str):
        self.gateway_base_url = gateway_base_url.rstrip("/")
        self.token_supplier = token_supplier
        self.org_id = org_id  # org-scoped -> x-org header, not a query param

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token_supplier.bearer_token()}",
            "Content-Type": "application/json",
            "x-org": self.org_id,  # gateway authz rule (template.rs:16)
        }

    def chat_completions(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        response = requests.post(
            f"{self.gateway_base_url}{self.api_path}/chat/completions",
            headers=self._headers(),
            json=request.model_dump(mode="json", by_alias=True, exclude_none=True),
            timeout=120,  # LLM calls are slow; the 30s base default is too tight
        )
        response.raise_for_status()
        return ChatCompletionResponse(**response.json())

    def complete(self, messages: list[dict], model: str, **kw: object) -> str:
        req = ChatCompletionRequest(model=model, messages=messages, **kw)
        resp = self.chat_completions(req)
        return resp.choices[0].message.content or ""
```

Facade wiring in `agency_sdk/client.py` (mirrors observability's cache/DCL, drops the extra guard):

```python
def __init__(self, ...):
    ...
    self._gateway: "AgencyGatewayClient | None" = None
    self._gateway_lock = threading.Lock()

def gateway(
    self,
    *,
    org_id: str,
    gateway_base_url: str | None = None,
    environment: str = "production",  # "production" | "test"
) -> "AgencyGatewayClient":
    """Build (once) an OpenAI-compatible LLM gateway client bound to this AgencyClient.

    Targets the gateway's own Cloud Run host, not the control-plane base_url.
    When ``gateway_base_url`` is omitted, resolves it from
    ``GET /api/agentgateways?o={org_id}`` on this client's control-plane base_url,
    selecting ``production.url`` or ``test.url`` per ``environment``.
    """
    from agency_sdk.delegates.gateway_client import AgencyGatewayClient

    gw = self._gateway
    if gw is None:
        with self._gateway_lock:  # double-checked locking (client.py:87)
            gw = self._gateway
            if gw is None:
                url = gateway_base_url or self._discover_gateway_url(org_id, environment)
                gw = AgencyGatewayClient(
                    token_supplier=self.token_supplier,  # reuse shared creds (client.py:92)
                    gateway_base_url=url,
                    org_id=org_id,
                )
                self._gateway = gw
    return gw
```

`gateway_dto.py` — Pydantic v2 models, plain snake_case (OpenAI wire is snake_case-friendly, so no
alias generator; matches `files_dto.py` style per `CLAUDE.md:50`). Keep them permissive
(`model_config = ConfigDict(extra="allow")`) so upstream/agentgateway params we do not enumerate pass
through and unknown response fields do not break parsing:

```python
from pydantic import BaseModel, ConfigDict

class ChatMessage(BaseModel):
    role: str
    content: str | None = None

class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")   # temperature, max_tokens, response_format, ...
    model: str
    messages: list[ChatMessage]

class ChatChoice(BaseModel):
    model_config = ConfigDict(extra="allow")
    index: int = 0
    message: ChatMessage

class ChatCompletionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    choices: list[ChatChoice]
```

> **Caveat:** the exact request/response fields are agentgateway v1.3.1 upstream, not fixed by the gts
> repos (§2.1). `extra="allow"` is deliberately used so we do not over-constrain the wire format.

---

## 6. guideline-agent consumption

The seam is the `LLMClient` protocol (`guideline_agent/llm/clients.py:8-22`): `base_url` property,
`model` property, and `async def complete(self, messages, **kwargs) -> str`. The single dispatch site
is `DependencyRegistry.llm_client_for_task` (`guideline_agent/workflows/dependencies.py:90-100`).
Everything downstream (stages, `_llm_for_task` at `extraction.py:857-889`, the `_TracedLLMClient`
wrapper at `extraction.py:129-219`, and all ~11 `.complete()` call sites) is provider-agnostic and
needs **no** change.

### 6.1 New `GatewayLLMClient` (native async)

Add `guideline_agent/llm/gateway_client.py` — a native-async client (matching the agent's existing
Claude/Gemini `httpx.AsyncClient` clients), reusing `CredentialsSupplier` for the rotating JWT and
sending `x-org`:

```python
from __future__ import annotations
import httpx
from agency_sdk.credentials import CredentialsSupplier
from guideline_agent.config import GatewayProviderConfig


class GatewayLLMClient:
    """LLMClient over the org's agentgateway (OpenAI-compatible)."""

    def __init__(self, provider: GatewayProviderConfig, creds: CredentialsSupplier,
                 http_client: httpx.AsyncClient | None = None) -> None:
        self._provider = provider
        self._creds = creds
        self._http = http_client or httpx.AsyncClient(
            base_url=provider.base_url.rstrip("/"),
            timeout=httpx.Timeout(connect=30.0, read=300.0, write=120.0, pool=30.0),
        )

    @property
    def base_url(self) -> str:
        return self._provider.base_url

    @property
    def model(self) -> str:
        return self._provider.model

    async def complete(self, messages: list[dict], **kwargs: object) -> str:
        payload = {"model": self._provider.model, "messages": messages, **kwargs}
        resp = await self._http.post(
            "/v1/chat/completions",
            json=payload,
            headers={
                "Authorization": f"Bearer {self._creds.bearer_token()}",  # rotating m2m JWT
                "x-org": self._provider.org_id,                            # gateway authz (template.rs:16)
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"] or ""
```

Notes:

- `creds.bearer_token()` is sync but cheap (returns a cached token, only re-mints ~30s before expiry,
  `credentials.py:26-54`); calling it inside `async complete` is acceptable. If the occasional
  blocking re-mint POST (`credentials.py:37`) matters under load, wrap that one call in
  `asyncio.to_thread` — see §8.
- `org_id` is a **string** (matches how observability carries it, `config.py`/`config.json`
  `"org_id":"2"`), because the gateway compares `x-org` as a string (`template.rs:16`).
- Reuse the **same** `CredentialsSupplier` the agent already builds in `control_plane.py:37` so there
  is one cached token across prompts, observability, and LLM.

### 6.2 Config plumbing — a new `gateway` provider

Three mechanical edits (mirrors how the three existing providers are wired):

> **Naming caveat (verified).** `config.py` already defines an unrelated `GatewayRegistryConfig`
> (`config.py:~160`, under the "Gateway / service configs" section) for the ontology/service registry
> — nothing to do with the LLM gateway. To avoid confusion, name the new class
> **`AgentGatewayProviderConfig`** (mirrors the control-plane vocabulary `AgentGateway…`) rather than a
> bare `GatewayProviderConfig`. Also note `AgentConfig.organisation` is an **`int`** (`config.py:276`)
> while the gateway needs the org as a **string** for `x-org`; either add a `str` `org_id` on the
> provider config (shown below) or `str(config.organisation)` at construction. The sketches below keep
> the shorter `GatewayProviderConfig` for brevity — use the disambiguated name in implementation.

1. **`config.py`** — add `GatewayProviderConfig` (impl name: `AgentGatewayProviderConfig`) next to the
   existing provider classes (`config.py:48-75`):

   ```python
   class GatewayProviderConfig(BaseModel):
       """Config for the org's agentgateway (OpenAI-compatible LLM routing)."""
       default: bool = False
       base_url: str          # the resolved prod/test gateway run.app URL
       model: str             # the virtual-model name, e.g. "biglambda1" (§2.3)
       org_id: str            # sent as the x-org header
       purpose: str = ""
   ```

   Register it in `_PROVIDER_CLASSES` (`config.py:78-82`):

   ```python
   _PROVIDER_CLASSES = {
       "llamacpp": LlamaCppProviderConfig,
       "gemini": GeminiProviderConfig,
       "claude": ClaudeProviderConfig,
       "gateway": GatewayProviderConfig,   # <-- new
   }
   ```

   Without this registration, `_parse_llms` (`config.py:96-97`) would pass an unknown `"gateway"` key
   through as raw dicts (a latent bug surface) — so this line is required.

2. **`dependencies.py`** — one `isinstance` branch in `llm_client_for_task`
   (`dependencies.py:94-99`). The registry already holds `self.config` (`dependencies.py:43`), so the
   shared creds are reachable via `control_plane.py`:

   ```python
   if isinstance(provider, GatewayProviderConfig):
       creds = self._gateway_creds()   # build once from self.config.control_plane, cache on deps
       return GatewayLLMClient(provider, creds)
   ```

   `_gateway_creds()` builds a single `CredentialsSupplier(auth_url, client_id, client_secret)` from
   `self.config.control_plane` (same values `build_agency_client` reads, `control_plane.py:26-37`) and
   caches it on the registry so all tasks share one token.

3. **`config.json` / `config.example.json`** — add a `gateway` entry under `llms` and point profiles
   at it. **No `ModelRegistry` change** — it resolves `llm_task_profiles[task] → provider_name →
   llms[provider_name]` generically and returns the `default=True` entry
   (`model_registry.py:get`, 51-69).

   ```json
   "llms": {
     "gateway": [
       {
         "default": true,
         "base_url": "${GATEWAY_BASE_URL}",
         "model": "biglambda1",
         "org_id": "2"
       }
     ]
   },
   "llm_task_profiles": {
     "document_classification": "gateway",
     "entity_normalization_judge": "gateway",
     "rule_summary": "gateway",
     "rule_detail": "gateway",
     "rule_judge": "gateway",
     "dbq_field_nl": "gateway",
     "valueset_reranker": "gateway"
   }
   ```

   Per-task model override: if different tasks need different virtual models, give each its own
   `gateway` provider entry keyed by name, or (cleaner) point every task at the `"*"` catch-all
   default provider on the gateway side (§2.3) and keep one `model` here. See §8.

### 6.3 Tracing still wraps it, unchanged

`_llm_for_task` (`extraction.py:857-889`) wraps whatever `llm_client_for_task` returns in
`_TracedLLMClient(task_name, client, trace, tracer)` (`extraction.py:888`). `_TracedLLMClient` reads
`base_url`/`model` via `getattr(..., default)` (`extraction.py:142-148`), starts the `llm.complete`
span, and `await self._client.complete(messages, **kwargs)` (`extraction.py:177`), forwarding kwargs
verbatim. So `GatewayLLMClient` inherits tracing for free; the `client` span attribute becomes
`"GatewayLLMClient"`. **Zero changes** to the wrapper, `_llm_for_task`, `ModelRegistry`, or any stage.

### 6.4 Minimal swap points (file:line)

- New file: `guideline_agent/llm/gateway_client.py` (satisfies `clients.py:8-22`).
- `guideline_agent/config.py:48-82` — add `GatewayProviderConfig` + register in `_PROVIDER_CLASSES`.
- `guideline_agent/workflows/dependencies.py:94-99` — one `isinstance` branch.
- `config.json` / `config.example.json` — `llms.gateway` + `llm_task_profiles`.

Untouched: `guideline_agent/llm/clients.py`, `guideline_agent/workflows/extraction.py:129-219` and
`:857-889`, `guideline_agent/llm/model_registry.py`, all `.complete()` call sites.

---

## 7. Config & migration

### 7.1 New config keys

- `llms.gateway[]`: `{ default, base_url, model, org_id, purpose? }` (§6.2).
- Prod/test selection is expressed by **which URL `base_url` holds** — set it to the production
  `run.app` URL in production deployments and the `-test` URL in staging. If using SDK discovery
  (§4), the selector is `environment: "production" | "test"` instead of a literal URL.
- `org_id` reuses the existing org string already present for observability
  (`config.json` `observability.org_id = "2"`); the control-plane creds reuse the existing
  `control_plane` block (`auth_url` / `base_url` / `client_id` / `client_secret`,
  `control_plane.py:26-29`). **No new secrets are introduced** for the gateway path.

### 7.2 Running test in staging, prod in production

- **Staging agent config** → `llms.gateway[].base_url = <test run.app URL>` (or SDK
  `environment="test"`). Test serves the latest **draft** (§3.3), so config changes published via
  "Save to test" are exercised in staging before promotion.
- **Production agent config** → `llms.gateway[].base_url = <production run.app URL>` (or
  `environment="production"`). Production serves the **published** version.
- Promotion flow: edit the gateway config → **Save to test** (`command: save`) → validate in staging →
  **Publish to production** (`command: publish`). Rollback via `rollback_production`; discard a bad
  draft via `rollback_draft` (§3.3).

### 7.3 Backward-compat and rollback to direct clients

- The three existing providers (`llamacpp`/`gemini`/`claude`) remain fully wired. Routing is purely a
  config choice: flip `llm_task_profiles` entries back from `"gateway"` to `"gemini"` (etc.) and the
  agent reverts to direct provider calls with **no code change**. Keep this as the rollback lever
  until the gateway path is proven.
- Roll out per task, not big-bang: point one low-risk task (e.g. `document_classification`) at
  `"gateway"` first, verify traces/outputs, then migrate the rest.

### 7.4 Secret removal

Once all `llm_task_profiles` point at `"gateway"` and the gateway path is validated, the upstream
provider keys (`${...}` for `openai`/`gemini`/`claude` in the agent's config/secrets) can be **dropped
from the agent** — the upstream secret now lives only in the org's gateway config
(`llm.providers[].params.apiKey`, resolved by the control plane from the org variable store,
`config.rs:91-113`). This is the security win in §1. Do the secret removal as a **separate final
step** after the profiles have been stable on `"gateway"` for a full run, so rollback (§7.3) stays
available until then.

---

## 8. Risks / open questions / decisions needed

1. **Token audience for gateway vs control-plane (VERIFY BEFORE SHIP).** The gateway requires
   `aud` to contain **`account`** (`config.rs:31`, `template.rs:20-21`), and the control-plane
   middleware validates against `security.oauth.audience` (`auth_middleware.rs:51,86`). These may be
   **different audience values**. The JWT `CredentialsSupplier` mints must satisfy the gateway's
   `account` audience. **Action:** confirm the Keycloak client used for m2m issues tokens with
   `account` in `aud` (or that the gateway accepts the client's audience). If not, we need a separate
   client/scope for the gateway Bearer. This is the single most important pre-ship check.

2. **Sync vs async — settled, but note the blocking re-mint.** Decision: SDK sync client;
   guideline-agent uses a native-async `GatewayLLMClient` reusing `CredentialsSupplier` (§5.4, §6.1).
   Residual: `bearer_token()` does a blocking `requests.post` on re-mint (`credentials.py:37`). Under
   high concurrency this briefly blocks the event loop ~once per token lifetime. **Decision needed:**
   accept it (simplest) or wrap the re-mint in `asyncio.to_thread`. Recommend accept for v1.

3. **Per-task model naming.** The 7 canonical tasks (`model_registry.py:9-17`) plus the config-only
   extras all currently route to one provider. Under the gateway, each task's `"model"` must be a
   valid virtual-model name (§2.3). **Decision:** simplest is one `"*"` catch-all default provider on
   the gateway → every task sends the same `model` string and it routes through. If tasks need
   distinct upstreams, declare per-task virtual models and add per-task `gateway` provider entries.
   Recommend starting with the catch-all.

4. **`valueset_reranker` is NOT an LLM in its primary path.** The valueset reranker's main path is a
   **CrossEncoder** (local model), with an LLM only as a fallback task
   (`extraction.py:905`, `valuesets.py:740`). **Do not** assume routing `valueset_reranker` to the
   gateway moves the CrossEncoder — it only affects the LLM fallback. Keep the CrossEncoder as-is;
   only the fallback `complete()` uses the gateway.

5. **Discover vs configure the URL.** Decision (§4): explicit `gateway_base_url`/`base_url` for v1;
   discovery via `GET /api/agentgateways` as the default-when-omitted in a later rc. Open: whether the
   agent should discover once at startup and cache, or per-run.

6. **Timeouts / retries / streaming.** Existing agent clients retry on 429/5xx with backoff
   (`claude_client.py:53-77`) and read-timeout 300s. `GatewayLLMClient` should mirror the same retry
   posture (the gateway proxies to the same upstreams, so the same 429/5xx handling applies).
   **Streaming** (`stream=true`, SSE) is out of scope for v1 — none of the current `.complete()`
   callers stream, and bridging SSE through the SDK's sync stack is painful. Leave as a future seam.

7. **Error mapping.** Both the SDK client and the agent client use `raise_for_status()` (no custom
   exception wrapping, per SDK `CLAUDE.md` conventions). Gateway auth failures surface as 401
   (bad/expired JWT) or 403 (missing/wrong `x-org`); model-not-found surfaces as an upstream/gateway
   4xx. **Decision needed:** whether the agent should special-case 403-`x-org` vs 401-JWT for clearer
   operator errors, or let `raise_for_status` bubble. Recommend bubble for v1, add a targeted message
   if 403s show up in practice.

8. **Gateway policy is a mutable default, not an invariant (from verification, §2.2).** A gateway
   whose config was `save`d could have weakened/removed `x-org`/`jwtAuth`. Our client always sends
   both, so it works against the default. Do not treat the gateway as the sole auth boundary in threat
   modeling; the org's published config is authoritative.

9. **Wire format not owned by gts (from verification, §2.1).** The exact OpenAI request params and
   response JSON are agentgateway v1.3.1 upstream. DTOs use `extra="allow"` (§5.5) to avoid
   over-constraining. Validate the real response shape against a live gateway before locking DTOs.

---

## 9. Phased implementation plan

Each phase is independently verifiable.

### Phase 1 — SDK: add `AgencyGatewayClient` + `client.gateway(...)`, release an rc

Scope (in `gts-agency_python-sdk`):
1. `agency_sdk/delegates/gateway_client.py` (`AgencyGatewayClient`, sync `requests`, §5.5).
2. `agency_sdk/delegates/gateway_dto.py` (Pydantic v2, `extra="allow"`, §5.5).
3. `agency_sdk/client.py` — cache fields + `gateway(*, org_id, gateway_base_url=None,
   environment="production")` facade, DCL cache modeled on observability (`client.py:56-104`); no
   optional extra, no `require_*_deps()`.
4. Optional discovery helper `_discover_gateway_url(org_id, environment)` hitting
   `GET /api/agentgateways?o={org}` and reading `production.url`/`test.url`
   (`agent_gateway_dto.rs:24-47`).
5. Docs/examples per repo convention: `docs/gateway.md` + `docs/gateway_design.md`,
   `examples/quick_gateway.py` (env-driven), update `README.md` "Delegate Clients".
6. Offline tests under `agency_sdk/test/` (conftest stubs `requests`): assert the request carries the
   `x-org` header, the gateway host URL, and the Bearer token (mirroring `test_base_client.py:34-35`).
7. Bump `pyproject.toml` version, cut `v0.0.1rc11` → publishes to PyPI via the OIDC trusted publisher
   (`CLAUDE.md:67-69`).

**Verification:** unit tests green; `examples/quick_gateway.py` against the local gateway
(`gts-local-environment` config.yaml) returns a completion with `x-org` + JWT.

### Phase 2 — guideline-agent: add the `gateway` provider (direct/native-async path)

Scope (in `gts-guideline-agent`):
1. `guideline_agent/llm/gateway_client.py` (`GatewayLLMClient`, native async `httpx`, reuses
   `CredentialsSupplier`, §6.1).
2. `guideline_agent/config.py` — `GatewayProviderConfig` + `_PROVIDER_CLASSES` registration (§6.2).
3. `guideline_agent/workflows/dependencies.py:94-99` — one `isinstance` branch + shared-creds helper.
4. Route **one** low-risk task (`document_classification`) to `"gateway"` in `config.json`; keep the
   rest on `gemini`.
5. Tests: a `GatewayLLMClient` test asserting `x-org` + Bearer + `/v1/chat/completions`; confirm
   `_TracedLLMClient` wraps it (span attribute `client == "GatewayLLMClient"`).

**Verification:** a live pipeline run with `document_classification` on the gateway produces correct
output and a `llm.complete` span; diff against the same run on `gemini`.

> **Decision to make at Phase 2:** whether guideline-agent uses its own native-async
> `GatewayLLMClient` (recommended, §5.4 option 2) or the SDK's sync `AgencyGatewayClient` wrapped in
> `asyncio.to_thread`. Given the agent is already `httpx.AsyncClient`-based, the native-async client
> is the better fit and Phase 2 assumes it. The SDK client from Phase 1 still ships for other
> (sync) consumers.

### Phase 3 — full migration + secret removal

1. Point all `llm_task_profiles` at `"gateway"` (except the CrossEncoder-primary `valueset_reranker`
   consideration, §8.4).
2. Run a full extraction; validate outputs + traces against the pre-migration baseline.
3. Deployment split: staging → test URL, production → production URL (§7.2).
4. **After a stable full run:** drop `openai`/`gemini`/`claude` keys from the agent's
   config/secrets (§7.4). Keep the profile-flip rollback lever (§7.3) until this step.

**Verification:** full-run output parity vs baseline; secrets grep clean; rollback rehearsed (flip one
profile back, run one task).

---

## Appendix — key file:line index

**Gateway (control plane, Rust):**
- Rendered auth/authz/JWT policy: `gts-agency-control/src/service/agentgateway/template.rs:10-30`;
  org-id substitution proof `:76-94`.
- Issuer/JWKS/audience (`account`) resolution:
  `gts-agency-control/src/service/agent_gateway_service/config.rs:12-32`.
- Virtual-model / provider rendering: `config.rs:54-89`, `:115-234`; default `"*"` `:222-229`.
- Supported provider types: `gts-agency-control/src/service/model_provider_service.rs:522-527`.
- Service/URL naming (`-test` infix):
  `gts-agency-control/src/service/infrastructure/gcp_agentgateway_runtime.rs:138-144`; URL read-back
  `:458-466`; ports 4000/15000 `:352-379`; test scaling `:398-403`.
- Two-env model: `gts-agency-control/src/model/agent_gateway_deployment.rs:31-57`.
- Lifecycle verbs: `gts-agency-control/src/service/agent_gateway_service/commands.rs:72-90`,
  `:147-225` (enable), `:227-280` (save), `:282-312` (publish), `:314-339` (rollback_draft),
  `:341-372` (rollback_production), `:374-402` (disable).
- Discovery DTO (`production.url`/`test.url`):
  `gts-agency-control/src/service/agentgateway/agent_gateway_dto.rs:24-47`; population
  `queries.rs:95-183`. Routes: `handler/agent_gateway.rs:63-70`, mounted `main.rs:334-335`.
- Live local config (contract corroboration):
  `gts-local-environment/configurations/agentgateway/config.yaml`.

**SDK (`gts-agency_python-sdk`):**
- Facade + observability precedent: `agency_sdk/client.py:17-104` (cache `:32-33`, reuse creds `:92`,
  host default `:96`, DCL `:82-89`).
- Base delegate plumbing, header dict (no `x-org`): `agency_sdk/delegates/base_client.py:26-52`
  (headers `:40-42`, URL compose `:45`, 30s timeout `:49`).
- Rotating m2m JWT: `agency_sdk/credentials.py:26-54`.
- Optional-extra guard precedent (NOT used for gateway): `agency_sdk/observability/__init__.py:37-48`.

**guideline-agent (`gts-guideline-agent`):**
- `LLMClient` protocol (seam): `guideline_agent/llm/clients.py:8-22`.
- Dispatch site (primary swap): `guideline_agent/workflows/dependencies.py:90-100`.
- Provider configs + `_PROVIDER_CLASSES`: `guideline_agent/config.py:48-82`.
- Existing async httpx client (pattern for `GatewayLLMClient`): `guideline_agent/llm/claude_client.py`
  (async `:38`, retries `:53-77`), `guideline_agent/llm/gemini_client.py:24,79`.
- Shared creds factory (reuse): `guideline_agent/control_plane.py:16-38`.
- Tracing wrapper (unchanged): `guideline_agent/workflows/extraction.py:129-219`; `_llm_for_task`
  `:857-889`.
- ModelRegistry (unchanged, generic resolve): `guideline_agent/llm/model_registry.py:9-17,51-69`.

---

## 10. Live validation results & open decisions (2026-07-06)

The `/v1/chat/completions` contract was validated end-to-end against the local
`cr.agentgateway.dev/agentgateway:v1.3.1` container (config
`gts-local-environment/configurations/agentgateway/config.yaml`; models `[{name:'*' →
biglambdachat}]`; upstream `chat.biglambda.groundtruthsystems.com`).

- **LLM endpoint is host port `4000`, not 3000.** The compose comment "3000 = Main
  LLM/Proxy" is stale; the `llm:` config shorthand binds the gateway proxy to 4000
  (15000 = admin UI). Production is a single Cloud Run URL where `/v1/chat/completions`
  is the path — the port only matters locally.
- **Auth/authz confirmed:** Bearer + `x-org:2` → **200**; no `Authorization` → **401**
  (`authentication failure: no bearer token found`); Bearer + no `x-org` → **403**;
  Bearer + wrong `x-org` → **403** (`authorization failed`). **Error bodies are plain
  text, not JSON** — 401 = auth/token, 403 = authz/x-org. The SDK propagates via
  `raise_for_status()` and must not assume a JSON error body.
- **`aud: account` is satisfied out of the box.** Every Keycloak service-account
  client's `client_credentials` token carries `aud` including `account` plus `iss/sub/exp`
  (e.g. `agency-system`/`api-key-2c018ce7` → `"account"`; `agency-backend` →
  `["realm-management","account"]`). The §8.1 pre-ship blocker is resolved:
  `CredentialsSupplier` needs **no** extra scope/audience config.
- **Response shape:** standard OpenAI — keys `id/model/object/created/choices/usage/
  system_fingerprint/timings`; text at `choices[0].message.content`. The `model` field
  echoes the **upstream** name (`Qwen3.6-27B…gguf`), not the virtual name sent — the
  `"*"` catch-all routing works.
- **Qwen reasoning quirk persists through the gateway:** too-small `max_tokens` →
  empty `content` + `reasoning_content` only + `finish_reason:length`. The gateway is
  transparent; handling stays in the caller (same as `llamacpp_client`'s
  `chat_template_kwargs:{enable_thinking:False}`). Hence `ChatCompletionResponse.content`
  returns `""` in that case.

**Open decisions for the SDK track (pending confirmation):**
1. Include the `/api/agentgateways` discovery delegate now (source-modeled from the
   control-plane Rust DTO; **not** live-verifiable locally — the local control-plane
   image predates the gateway feature), or defer it and ship v1 with explicit
   `gateway_base_url` only. *Recommendation: include, flagged as verification-deferred.*
2. Bump `pyproject.toml` to `rc11` within the track, or leave the release (bump + tag)
   to a separate step after merge. *Recommendation: leave release to after merge.*

**Implementation status:** SDK branch `feat/agent-gateway-client` created (off `main`,
tag `rc10`); no code written yet. To be built as the Conductor track
`agent_gateway_client_20260706` in `gts-agency_python-sdk`. Local gateway container left
running on `:4000` for E2E (`examples/quick_gateway.py`).
