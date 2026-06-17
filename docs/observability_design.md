# Observability Tracing — SDK Design

Design blueprint for folding `gts-demo-agent`'s OpenTelemetry tracing/logging
bootstrap into the Agency SDK. This is the Phase 1 deliverable of the
`observability_tracing_20260617` track; Phases 2–7 implement it.

Source of truth for the original mechanism: `gts-demo-agent`
`docs/observability.md` and `demo/common/observability.py`. This document records
the design **decisions** for the SDK port, not a copy of the source.

---

## 1. Research catalogue — the 6 load-bearing mechanisms

The demo doc identifies six "counterintuitive but load-bearing" mechanisms. How
each maps into the SDK:

| # | Mechanism | SDK treatment |
| --- | --- | --- |
| 1 | Real recording `TracerProvider` (else log records get `trace_id=0`) | **Verbatim.** `init()` builds a real `TracerProvider`; never a no-op/proxy. Do **not** call `trace.set_tracer_provider()` — hold it explicitly and return its tracer; correlation is driven by OTel context (contextvars), not the global provider. |
| 2 | Per-request auth hooks, not a static header | **Verbatim (adapted to one token source).** OTLP `requests` exporter gets `session.auth = BearerTokenAuth(...)`; the Langfuse `httpx` client gets the httpx mirror. The static `Authorization` from `build_headers` is only a seed. |
| 3 | Lazy/cached/early-refreshed, thread-safe token | **Adapt — unify on `CredentialsSupplier`.** Drop the demo's `KeycloakTokenProvider`. The hooks pull from the SDK's existing `CredentialsSupplier`. Add a small early-refresh buffer to it (§6) so a long-running export never sends a token that expires mid-flight. |
| 4 | Auth fallback chain (OTEL header > bearer > Langfuse Basic) | **Keep, configurable.** Since `AgencyClient` always carries a `CredentialsSupplier`, the bearer path is always available; the explicit-header override and Basic-auth fallback remain for non-Keycloak backends. |
| 5 | Root span + context propagation | **Adapt — wrap in `agent_run()`.** The "open a recording root span first" rule is encoded in a context manager so callers cannot orphan logs/spans. (The demo's asyncio-context-copy detail was LlamaIndex-specific; the generic rule is what we keep.) |
| 6 | Simple processors are synchronous (demo-oriented) | **Adapt — make it a parameter.** `processor="simple"` (default, deterministic) or `"batch"` (production). |

The **only** framework coupling in the demo (`LlamaIndexInstrumentor`) is
**dropped**. Per the track decision, this port ships framework-agnostic manual
spans only; an instrumentor registry is left as a future extension point (§7).

---

## 2. Module layout

```
agency_sdk/
  observability/
    __init__.py     # public exports: Observability (+ DEFAULT_*_PATH)
    auth.py         # BearerTokenAuth (requests) — always importable (requests is core)
    bootstrap.py    # Observability: init() / agent_run() / langfuse_client() / shutdown()
```

- `auth.py` depends only on `requests` (a core dep), so it is importable without
  the `[observability]` extra and its hook logic is unit-testable in isolation.
- The httpx mirror auth is created **lazily inside `langfuse_client()`** (httpx
  arrives with the `langfuse` extra), so `auth.py`/`bootstrap.py` import without
  httpx present.
- `bootstrap.py` imports OpenTelemetry **inside `init()`** (and TYPE_CHECKING
  blocks), so the module imports cleanly without the extra; only `init()` needs
  the heavy deps.

---

## 3. Public API surface

### 3.1 Facade entry point (only supported entry — per track decision)

```python
class AgencyClient:
    def observability(
        self,
        service_name: str,
        service_version: str = "unknown-0",
        *,
        host: str | None = None,            # defaults to self.base_url
        environment: str = "development",
        org_id: str | None = None,
        processor: str = "simple",          # "simple" | "batch"
        langfuse_public_key: str | None = None,
        langfuse_secret_key: str | None = None,
    ) -> "Observability": ...
```

- Lazily imports `agency_sdk.observability`; if the extra is missing, raises a
  clear, actionable error (§5).
- Binds the client's shared `CredentialsSupplier` and defaults the OTLP/Langfuse
  `host` to the client `base_url`.
- Caches the built instance on `self._observability`; repeated calls return the
  same object (idempotent setup).

### 3.2 `Observability`

```python
class Observability:
    def __init__(self, credentials, service_name, service_version="unknown-0", *,
                 host=None, environment="development", org_id="2",
                 processor="simple", logs_path=DEFAULT_LOGS_PATH,
                 traces_path=DEFAULT_TRACES_PATH, extra_headers=None,
                 langfuse_public_key=None, langfuse_secret_key=None,
                 langfuse_host=None, logger=None): ...

    def init(self) -> "Tracer | None": ...
    def agent_run(self, name: str, **attributes) -> "ContextManager[Span | None]": ...
    def langfuse_client(self) -> "Langfuse | None": ...
    def shutdown(self) -> None: ...      # also registered via atexit
```

- `from_config(...)` classmethod is **not** part of the supported surface (the
  facade is the only entry). It may exist privately to keep env-resolution logic,
  but the public path is `AgencyClient.observability(...)`.

### 3.3 Caller usage (the whole integration)

```python
client = AgencyClient(token_supplier=creds, base_url="http://localhost:13001")
obs = client.observability("gts-myagent", service_version)
tracer = obs.init()                       # exporters live; logging bridged
with obs.agent_run("agent.myagent", correlation_id=cid) as span:
    logger.info("working")                # stamped with the span's trace_id
    result = do_work()                    # any framework / raw SDK
langfuse = obs.langfuse_client()          # optional: prompt mgmt / scoring
```

---

## 4. Token unification (Mechanism 3)

- The hooks take a `token_supplier: Callable[[], str | None]`, **not** a
  `CredentialsSupplier` directly — keeps `auth.py` decoupled and trivially
  testable.
- `Observability` builds that callable as a safe wrapper around
  `credentials.bearer_token()` that catches exceptions and returns `None`, so a
  transient auth failure degrades to an untraced export rather than crashing the
  exporter thread.
- `build_headers` precedence stays: explicit `Authorization` in `extra_headers`
  (or `OTEL_EXPORTER_OTLP_HEADERS`) > bearer token > Langfuse Basic
  (`base64(public:secret)`); `x-org-id` always set.

---

## 5. Dependency strategy & graceful degradation

### 5.1 Optional extra

```toml
[project.optional-dependencies]
observability = [
    "opentelemetry-sdk>=1.27.0",
    "opentelemetry-exporter-otlp-proto-http>=1.27.0",
    "opentelemetry-instrumentation-logging>=0.48b0",
    "langfuse>=3.8.1",
]
```

`opentelemetry-api` arrives transitively with `-sdk`; `httpx` arrives with
`langfuse`. Dev/test installs use `pip install -e ".[dev,observability]"`.

### 5.2 Rules

1. `import agency_sdk` and `AgencyClient(...)` construction **never** import
   OpenTelemetry/Langfuse — guaranteed by lazy imports in `client.observability()`
   and inside `Observability.init()`.
2. Missing extra → `AgencyClient.observability(...)` raises a clear message:
   `"Observability support requires the optional dependency. Install it with: pip install gts-agency-python-sdk[observability]"`.
3. Exporter setup failure inside `init()` → log a warning, return `None`; the
   agent runs untraced.
4. No tracer → `agent_run()` is a `contextlib.nullcontext` yielding `None`.
5. `langfuse_client()` returns `None` when the package or keys are absent.

---

## 6. `CredentialsSupplier` decision

**Decision: add an early-refresh buffer; do NOT add an `insecure` flag now.**

- **Early-refresh buffer (adopt):** the demo refreshes ~30 s before real expiry
  so a token is never used mid-flight. The SDK's `CredentialsSupplier` currently
  treats a token as valid until the exact `exp`. For the long-running per-request
  export hook (Mechanism 2) this risks stamping a token that expires in transit.
  Add `refresh_buffer: float = 30.0` and treat the token as expired
  `refresh_buffer` seconds early. Backward compatible (only refreshes slightly
  sooner). This is a credentials-contract change → recorded in `tech-stack.md`.
- **`insecure`/`verify=False` (defer):** the local stack's token endpoint is
  typically plain HTTP, so TLS verification does not apply; for production,
  verifying is correct. Exporter-level TLS control, if ever needed, belongs to
  the observability module, not the shared credentials contract. Not changing the
  contract keeps the blast radius small.

---

## 7. Future extension point — instrumentor registry (not built this track)

`bootstrap.py` keeps an internal hook where a list of opt-in instrumentors
(LlamaIndex / Anthropic / Google GenAI) could be wired against the held
`TracerProvider`. This track ships **none** of them; the seam exists so a later
track can add `instrumentors=[...]` without reworking `init()`.

---

## 8. Testing strategy

All offline; no network. Dev env installs `[dev,observability]`.

| Phase | What it tests | How |
| --- | --- | --- |
| 2 | Packaging / lazy import | `import agency_sdk` + client construct with extra absent; `observability()` raises the clear error (simulate missing module via `sys.modules`) |
| 3 | Auth hooks + header/endpoint logic | `BearerTokenAuth` stamps a fresh token each call (fake supplier + fake request); `build_headers` precedence; endpoint resolution — pure unit tests |
| 4 | Telemetry pipeline + correlation | OTel `InMemorySpanExporter` / `InMemoryLogExporter`: started span has a valid (non-zero) context; a stdlib log inside it carries the span's trace_id; processor selectable; exporter failure → `init()` returns `None` |
| 5 | `agent_run` + facade | root span + attrs; nullcontext when tracing off; child span/log share trace_id; `observability()` binds the shared `CredentialsSupplier`, caches, defaults host to `base_url` |
| 6 | Langfuse helper | stub `langfuse` via `sys.modules`: client built with httpx bearer auth + shared span exporter; `None` when keys/package absent |

mypy strict: heavy deps are imported locally inside functions; annotations use
string/`TYPE_CHECKING` forms so the module type-checks without the extra
installed in the type-checker's environment.

---

## 9. Out of scope (restated from spec)

Framework instrumentors (registry seam only), metrics, CLI/TUI, migrating the
downstream agents, and backend infra changes.
