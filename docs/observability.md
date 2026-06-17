# Observability (OTLP tracing + log correlation)

The SDK can ship OpenTelemetry traces and correlated logs to a Langfuse backend,
authenticated with the **same** credentials the API client already uses. It is
opt-in: the heavy dependencies live behind an extra and are imported lazily, so
pure-API consumers are unaffected.

For the internal design and the rationale behind each mechanism, see
[observability_design.md](observability_design.md).

## Install

```bash
pip install gts-agency-python-sdk[observability]
```

Without the extra, importing the SDK and using the API clients works unchanged;
calling `client.observability(...)` raises a clear error telling you to install it.

## Quick start

```python
from agency_sdk.client import AgencyClient, CredentialsSupplier

credentials = CredentialsSupplier(auth_base_url=..., client_id=..., client_secret=...)
client = AgencyClient(token_supplier=credentials, base_url="http://localhost:13001")

obs = client.observability("gts-myagent")   # reuses credentials; host defaults to base_url
tracer = obs.init()                          # exporters live; stdlib logging bridged

with obs.agent_run("agent.myagent", correlation_id=cid) as span:
    logger.info("doing work")                # stamped with the span's trace id
    result = do_work()                       # any framework / raw SDK calls

langfuse = obs.langfuse_client()             # optional: prompt management / scoring
```

In Langfuse the run shows up as one trace (`agent.myagent` root with any nested
spans), and each log line attaches to the span active when it fired.

## API

`AgencyClient.observability(service_name, service_version="unknown-0", *, host=None,
environment="development", org_id="2", processor="simple", langfuse_public_key=None,
langfuse_secret_key=None) -> Observability`

- Built once and cached on the client; repeated calls return the same instance.
- `host` defaults to the client `base_url`; override for a separate OTLP backend.
- `processor`: `"simple"` (default, synchronous, deterministic) or `"batch"`
  (non-blocking, for production throughput).

`Observability`:

- `init() -> Tracer | None` — set up OTLP export + the stdlib log bridge and return
  a tracer. Returns `None` if exporter setup fails, so your agent keeps running
  untraced instead of crashing.
- `agent_run(name, **attributes)` — context manager that opens the recording root
  span (and sets attributes such as `correlation_id`). **Always wrap your run in
  this** so logs and child spans correlate. When tracing is off it is a no-op
  yielding `None`, so the same block still runs.
- `langfuse_client() -> Langfuse | None` — a Langfuse client authenticated through
  the same refreshing bearer token, or `None` when keys/package are absent.
- `shutdown()` — flush exporters and detach the log handler.

## Configuration & env overrides

`observability(...)` takes explicit options; these environment variables are also
honoured by the underlying exporters:

| Variable | Effect |
| --- | --- |
| `OTEL_EXPORTER_OTLP_HEADERS` | Extra static headers (`k=v,k2=v2`); an explicit `Authorization` here wins over the bearer token |

Authentication precedence for the OTLP `Authorization` header: explicit header >
the shared `CredentialsSupplier` bearer token > Langfuse Basic (from the project
public/secret keys). The bearer token is then refreshed on **every** export via a
per-request auth hook, so a long-running process never sends an expired token.

## Why `agent_run` matters

Log/trace correlation works because each exported log record carries the
`trace_id`/`span_id` of the span that is active when it fires. If no recording span
is active, logs get `trace_id = 0` and are orphaned. `agent_run` opens that root
span for you and degrades safely when tracing is off — so callers cannot get this
wrong.

## Migrating from a per-agent `OtelObservability`

If your agent currently carries its own `OtelObservability` bootstrap (as in
`gts-demo-agent`), replace it with the SDK:

| Before (inline bootstrap) | After (SDK) |
| --- | --- |
| `OtelObservability.from_config(config, name, ver).init()` | `client.observability(name, ver).init()` |
| separate `KeycloakTokenProvider` | the client's `CredentialsSupplier` (one shared token) |
| manual `tracer.start_as_current_span(...)` + attributes | `with obs.agent_run(name, **attrs):` |
| `build_langfuse_client()` | `obs.langfuse_client()` |

Framework auto-instrumentation (LlamaIndex / Anthropic / Google GenAI) is **not**
bundled by this SDK module; open spans manually via `agent_run` and the returned
tracer. The design leaves a seam for adding opt-in instrumentors later.

## Example

```bash
pip install gts-agency-python-sdk[observability]
export AGENCY_AUTH_URL=... AGENCY_API_URL=... AGENCY_CLIENT_ID=... AGENCY_CLIENT_SECRET=...
python examples/quick_observability.py
```
