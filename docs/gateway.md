# Agent Gateway Client

Route LLM traffic through the org's deployed **agentgateway** instead of holding
per-provider API keys. The SDK wires the auth (rotating Keycloak m2m JWT via the
shared `CredentialsSupplier`), the `x-org` routing header, and the gateway URL,
and hands you a standard official **`openai`** client with all of that
pre-attached — the upstream provider secret lives in the org's gateway config on
the control plane, and the agent holds **one** credential.

Design and full contract: [gateway_design.md](gateway_design.md) (live-validated §10).

> `openai` is a **core dependency** of this SDK — no extra to install. The gateway
> capability is openai-SDK-only; the SDK owns auth / `x-org` / URL wiring, and
> `openai` owns the LLM surface (completions, streaming, tools, structured
> outputs, retries, async).

## Entry point

`client.gateway(...)` returns a factory bound to your credentials, `org_id`, and
gateway URL; `openai_client()` / `async_openai_client()` hand back configured
clients:

```python
from agency_sdk.client import AgencyClient, CredentialsSupplier

credentials = CredentialsSupplier(auth_base_url=..., client_id=..., client_secret=...)
client = AgencyClient(token_supplier=credentials, base_url="http://localhost:13001")

gw = client.gateway(
    org_id="2",                                # sent as the x-org header (string compare)
    gateway_base_url="http://localhost:4000",  # the gateway's own host, NOT base_url
)

oai = gw.openai_client()                       # standard openai.OpenAI, pre-wired:
                                               #   base_url = {gateway}/v1
                                               #   x-org default header
                                               #   fresh rotating bearer stamped per request
```

`gateway(...)` is cached per identity — `(org_id, gateway_base_url)` or
`(org_id, environment)`: repeated calls with the same arguments return the same
factory (thread-safe, double-checked locking), while a different org,
environment, or URL builds its own, so one process can hold correctly routed
gateways for several orgs at once.

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

## Using the client

Everything in the openai SDK docs works as-is — completions, streaming, tool
calling, structured outputs, built-in retries:

```python
r = oai.chat.completions.create(
    model="biglambda1",          # a virtual-model name from the org's gateway config
    messages=[{"role": "user", "content": "Summarize this rule ..."}],
    temperature=0.0,
)
print(r.choices[0].message.content)

# Streaming
for chunk in oai.chat.completions.create(model="biglambda1", messages=[...], stream=True):
    delta = chunk.choices[0].delta.content
    if delta:
        print(delta, end="", flush=True)

# Tools / structured outputs — plain openai SDK
r = oai.chat.completions.create(model="biglambda1", messages=[...], tools=[...])
```

Async consumers (e.g. an httpx-based agent) take the async variant:

```python
aoai = gw.async_openai_client()
r = await aoai.chat.completions.create(model="biglambda1", messages=[...])
```

Notes:

- Each call builds a **new** client — the caller owns its lifecycle
  (`oai.close()` / `await aoai.close()` when done, or reuse one per process).
- Constructor kwargs pass through: `gw.openai_client(max_retries=0, timeout=30)` —
  except the four the factory wires itself (`base_url`, `api_key`,
  `default_headers`, `http_client`); passing those raises `TypeError` (build your
  own client, below, if you must control them).
- Errors are openai's exception taxonomy (`openai.APIStatusError`; 403 = `x-org`
  rejected, 401 = bad/expired JWT).

## Building your own openai client (advanced)

If you need to control the four reserved kwargs, wire the three things the
factory does — the gateway `base_url` (+ `/v1`), the `x-org` header, and
rotation-safe auth — yourself:

```python
import httpx, openai
from agency_sdk.auth_hooks import make_httpx_bearer_auth

oai = openai.OpenAI(
    base_url="http://localhost:4000/v1",
    api_key="unused",  # the auth hook overrides Authorization per request
    default_headers={"x-org": "2"},
    http_client=httpx.Client(auth=make_httpx_bearer_auth(credentials.bearer_token)),
)
```

> Why the auth hook and not `api_key=<token>`? The Keycloak m2m token expires in
> minutes; a construction-time key goes stale and long-lived processes start
> seeing 401s. `make_httpx_bearer_auth` re-reads the cached, auto-refreshing
> token on every request.

## Auth and org scoping

Every request carries:

| Header | Value | Failure mode |
|---|---|---|
| `Authorization` | `Bearer <rotating Keycloak m2m JWT>` | missing/invalid → **401** |
| `x-org` | the org id as a decimal string | missing/wrong → **403** |

- The header is **`x-org`**, not `x-org-id` (that is the observability OTLP header).
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

The script is self-verifying (exit non-zero on failure): sync completion,
streaming, async completion, and the wrong-`x-org` → 403 negative check.
