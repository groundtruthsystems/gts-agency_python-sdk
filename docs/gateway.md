# Agent Gateway Client

Route LLM traffic through the org's deployed **agentgateway** instead of holding
per-provider API keys: the SDK wires the auth (rotating Keycloak m2m JWT via the
shared `CredentialsSupplier`), the `x-org` routing header, and the gateway URL —
the upstream provider secret lives in the org's gateway config on the control
plane, and the agent holds **one** credential.

Design and full contract: [gateway_design.md](gateway_design.md) (live-validated §10).

## Entry point

Everything hangs off one accessor — auth rotation, `x-org`, and URL resolution
are wired here once:

```python
from agency_sdk.client import AgencyClient, CredentialsSupplier

credentials = CredentialsSupplier(auth_base_url=..., client_id=..., client_secret=...)
client = AgencyClient(token_supplier=credentials, base_url="http://localhost:13001")

gw = client.gateway(
    org_id="2",                                # sent as the x-org header (string compare)
    gateway_base_url="http://localhost:4000",  # the gateway's own host, NOT base_url
)
```

`gateway(...)` is cached per identity — `(org_id, gateway_base_url)` or
`(org_id, environment)`: repeated calls with the same arguments return the same
instance (thread-safe, double-checked locking), while a different org,
environment, or URL builds its own client, so one process can hold correctly
routed gateways for several orgs at once.

**URL vs environment — mutually exclusive.** Either give the URL, or give the
environment (with discovery) — never both (`ValueError` otherwise):

```python
gw = client.gateway(org_id="2", gateway_base_url="https://agentgateway-org-2-....run.app")
gw = client.gateway(org_id="2")                       # discovery, production slot (default)
gw = client.gateway(org_id="2", environment="test")   # discovery, test slot
```

Discovery calls `GET {base_url}/api/agentgateways?o={org}` once with the shared
bearer (requires the gateway to be enabled and control-plane read access;
live-verified 2026-07-07). Explicit `gateway_base_url` remains the recommended
production posture — no startup round-trip on the control plane.

## Choosing your tier

All tiers share the same `gw` object (same cached token, same `x-org`, same URL):

| Your situation | Use | Extra deps |
|---|---|---|
| Script / notebook; one-shot or simple streaming | **A. Built-in:** `gw.complete()` / `gw.complete_stream()` | none |
| Tools, structured outputs, retries, async — full OpenAI surface | **B. Full-feature:** `gw.openai_client()` / `gw.async_openai_client()` | `[openai]` extra |
| Custom client lifecycle / special wiring | **C. Manual recipe** (below) | self-managed `openai` |

## Tier A — built-in zero-dependency client

The minimal option when you do not want the `openai` package. Sync `requests`,
token rotation handled automatically (a fresh cached token is read per request),
errors surface as `requests.HTTPError`.

```python
# One-shot
text = gw.complete(
    [{"role": "user", "content": "Summarize this rule ..."}],
    model="biglambda1",          # a virtual-model name from the org's gateway config
    temperature=0.0,
)

# Streaming (SSE) — yields assistant text deltas
for delta in gw.complete_stream([{"role": "user", "content": "..."}], model="biglambda1"):
    print(delta, end="", flush=True)

# Full chunk objects, if you need finish_reason/usage
for chunk in gw.chat_completions_stream(request):
    ...
```

Notes:

- Extra OpenAI params (`temperature`, `max_tokens`, `response_format`, ...) pass
  through; the DTOs are deliberately `extra="allow"` (wire format is agentgateway
  upstream).
- Passing `stream=True` to the one-shot `complete()`/`chat_completions()` raises
  an immediate `ValueError` pointing you to the streaming methods.
- No tool calling, no structured-output parsing, no retries — that is tier B.

## Tier B — full-feature via the official openai SDK

```bash
pip install "gts-agency-python-sdk[openai]"
```

```python
oai = gw.openai_client()          # standard openai.OpenAI, pre-wired:
                                  #   base_url = {gateway}/v1
                                  #   x-org default header
                                  #   fresh rotating bearer stamped per request
```

Everything in the openai SDK docs works as-is — completions, streaming, tool
calling, structured outputs, built-in retries:

```python
r = oai.chat.completions.create(model="biglambda1", messages=[...], temperature=0.0)

for chunk in oai.chat.completions.create(model="biglambda1", messages=[...], stream=True):
    ...

r = oai.chat.completions.create(model="biglambda1", messages=[...], tools=[...])
```

Async consumers (e.g. an httpx-based agent) take the async variant of the same
recipe:

```python
aoai = gw.async_openai_client()
r = await aoai.chat.completions.create(model="biglambda1", messages=[...])
```

Notes:

- Each call builds a **new** client — the caller owns its lifecycle
  (`oai.close()` / `await aoai.close()` when done, or reuse one per process).
- Constructor kwargs pass through: `gw.openai_client(max_retries=0, timeout=30)`.
- Errors are openai's exception taxonomy (`openai.APIStatusError`; 403 = `x-org`
  rejected, 401 = bad/expired JWT).
- The construction-time `api_key` is a placeholder; real auth is a per-request
  httpx hook reading the shared rotating token.

## Tier C — manual recipe (DIY openai client)

If you manage the openai client yourself, wire three things: the gateway
`base_url` (+ `/v1`), the `x-org` header, and rotation-safe auth. Three verified
rotation patterns, pick one:

```python
import httpx, openai
from agency_sdk.observability.auth import make_httpx_bearer_auth

# 1) RECOMMENDED — per-request auth hook (what tier B does for you):
oai = openai.OpenAI(
    base_url="http://localhost:4000/v1",
    api_key="unused",
    default_headers={"x-org": "2"},
    http_client=httpx.Client(auth=make_httpx_bearer_auth(credentials.bearer_token)),
)

# 2) Per-call header override:
oai.chat.completions.create(..., extra_headers={"Authorization": f"Bearer {credentials.bearer_token()}"})

# 3) Lightweight copy per call site:
oai.with_options(api_key=credentials.bearer_token()).chat.completions.create(...)
```

> Why not just `api_key=<token>`? The Keycloak m2m token expires in minutes; a
> construction-time key goes stale and long-lived processes start seeing 401s.
> `bearer_token()` is cached — per-request reads are cheap.

## Auth and org scoping

Every request (all tiers) carries:

| Header | Value | Failure mode |
|---|---|---|
| `Authorization` | `Bearer <rotating Keycloak m2m JWT>` | missing/invalid → **401** |
| `x-org` | the org id as a decimal string | missing/wrong → **403** |

- The header is **`x-org`**, not `x-org-id` (that is the observability OTLP header).
- Gateway error bodies are **plain text**, not JSON.
- The default Keycloak service-account token already satisfies the gateway's
  `account` audience — no extra scope/audience configuration.

## Production vs test

Each org has **two** gateway deployments with distinct, persistent URLs:
production serves the **published** config, test (`-test` infix) serves the
latest **draft**. Point staging agents at the test URL and production agents at
the production URL — via a literal `gateway_base_url` per deployment config, or
via `environment="test"` with discovery. There is no per-request switch.

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

The script is self-verifying (exit non-zero on failure): one-shot, native
streaming, the wrong-`x-org` → 403 negative check, and — when the `[openai]`
extra is installed — the openai-helper path.
