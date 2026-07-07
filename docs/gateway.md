# Agent Gateway Client

Route LLM traffic through the org's deployed **agentgateway** instead of holding
per-provider API keys: the SDK's `client.gateway(...)` returns an OpenAI-compatible
client that authenticates with the same rotating Keycloak m2m JWT the rest of the
SDK uses (`CredentialsSupplier`) and stamps the gateway's required `x-org` routing
header. The upstream provider secret lives in the org's gateway config on the
control plane — the agent holds **one** credential.

Design and full contract: [gateway_design.md](gateway_design.md) (live-validated §10).

## Quick start

```python
from agency_sdk.client import AgencyClient, CredentialsSupplier

credentials = CredentialsSupplier(auth_base_url=..., client_id=..., client_secret=...)
client = AgencyClient(token_supplier=credentials, base_url="http://localhost:13001")

gateway = client.gateway(
    org_id="2",                                   # sent as the x-org header (string compare)
    gateway_base_url="http://localhost:4000",     # the gateway's own host, NOT base_url
)

text = gateway.complete(
    [{"role": "user", "content": "Summarize this rule ..."}],
    model="biglambda1",                           # a virtual-model name from the org's gateway config
    temperature=0.0,
)
```

`gateway(...)` is cached: repeated calls return the same instance (thread-safe,
double-checked locking — same posture as `observability()`).

## The two call surfaces

- `complete(messages, model, **kw) -> str` — convenience; returns
  `choices[0].message.content`, or `""` when the assistant content is null
  (e.g. a reasoning model hit `max_tokens` before emitting content).
- `chat_completions(ChatCompletionRequest) -> ChatCompletionResponse` — the
  OpenAI-compatible primitive with the full parsed response.

Both POST to `POST {gateway_base_url}/v1/chat/completions` with a 120 s timeout.
Extra OpenAI params (`temperature`, `max_tokens`, `response_format`, ...) pass
through to the upstream provider — the DTOs are deliberately `extra="allow"`
because the exact wire format is agentgateway upstream, not fixed by gts.

## Auth and org scoping

Every request carries:

| Header | Value | Failure mode |
|---|---|---|
| `Authorization` | `Bearer <rotating Keycloak m2m JWT>` | missing/invalid → **401** |
| `x-org` | the org id as a decimal string | missing/wrong → **403** |

Notes:

- The header is **`x-org`**, not `x-org-id` (that is the observability OTLP header).
- Error bodies are **plain text**, not JSON. Errors propagate via
  `raise_for_status()` per SDK convention — no custom exception wrapping.
- The default Keycloak service-account token already satisfies the gateway's
  `account` audience; no extra scope/audience configuration is needed.

## Production vs test

Each org has **two** gateway deployments with distinct, persistent Cloud Run URLs:

- production `https://agentgateway-org-{id}-....run.app` — serves the **published** config
- test `https://agentgateway-org-{id}-test-....run.app` — serves the latest **draft**

Point staging agents at the test URL and production agents at the production URL
by setting `gateway_base_url` accordingly. There is no per-request switch.

## URL discovery (optional fallback)

When `gateway_base_url` is omitted, the SDK resolves it once from the control
plane, using the same shared bearer:

```python
gateway = client.gateway(org_id="2", environment="test")   # or "production" (default)
# -> GET {base_url}/api/agentgateways?o=2, reads the test.url / production.url slot
```

A clear `ValueError` is raised when the gateway is not enabled, the slot is still
provisioning, or `environment` is not `"production"`/`"test"`.

> **Verification status:** the discovery endpoint is source-modeled from the
> control-plane DTOs and covered by offline tests only — the local control-plane
> image predates the gateway feature. Prefer explicit `gateway_base_url` (it is
> also the more robust production posture: no startup round-trip).

## Async consumers

The SDK client is synchronous (`requests`), matching the SDK's single-HTTP-stack
invariant. Async consumers either wrap it —
`await asyncio.to_thread(gateway.complete, messages, model)` — or build a native
async `httpx` client reusing only `CredentialsSupplier` for the Bearer + `x-org`
headers (recommended for httpx-based agents; see design doc §5.4/§6.1).
Streaming (`stream=true`/SSE) is out of scope for v1.

## Local E2E

With the local stack up (Keycloak on :8080, agentgateway on :4000):

```bash
export AGENCY_AUTH_URL="http://localhost:8080/realms/agency/protocol/openid-connect/token"
export AGENCY_CLIENT_ID="<m2m client id>"
export AGENCY_CLIENT_SECRET="<m2m client secret>"
export AGENCY_ORG_ID="2"
export GATEWAY_BASE_URL="http://localhost:4000"
export GATEWAY_MODEL="biglambda1"

python examples/quick_gateway.py
```

The script is self-verifying (exit non-zero on failure) and includes a negative
check: the same valid JWT with a wrong `x-org` must be rejected with 403.
