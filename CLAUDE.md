# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Python client SDK for the GTS Agency platform. Provides typed HTTP clients for datasets, datasources, files, ontologies, prompts, and rules APIs.

- **Python:** >=3.12
- **Key deps:** requests, pydantic (v2), pyjwt, openai (the gateway routes through the official openai SDK)
- **Optional deps:** `[observability]` extra — opentelemetry-sdk, otlp-http exporter, instrumentation-logging, langfuse (lazy-imported); `[adk]` extra — `google-adk>=2.11,<3` (lazy-imported from `agency_sdk.adk`; pins OTel ≤1.42.1, which coexists with `[observability]`)

## Commands

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Install with dev + optional observability deps (observability tests skip without them)
pip install -e ".[dev,observability]"

# ...plus the ADK extra (agency_sdk.adk tests skip without it)
pip install -e ".[dev,observability,adk]"

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

**Entry point:** `AgencyClient` (in `client.py`) is a facade that composes eleven delegate clients (plus the lazily built gateway and observability capabilities), all sharing a `CredentialsSupplier` for OAuth2 client-credentials auth with automatic token caching/refresh.

**Delegate pattern:** Each API domain has a client + DTO module pair in `delegates/`:
- `datasets_client.py` / `datasets_dto.py` — CRUD + filesystem traversal + clone + the write lifecycle `draft` → `push` → `publish`. `clone_dataset` writes the **`.agency/` workspace** (`dataset.json` = GET body minus `audit`, `files.json` = path → `{hash: sha256, last_modified}`), which must stay **byte-compatible with `gts-cli`** so either tool can push the other's clone. **Push has no RPC**: it is a client-side hash diff, then one multipart `PUT filesystem/_upload` per changed file, then one `del` per removed file, and `files.json` is rewritten only after every call succeeds. All local refusals (local `status != 0`, id mismatch, bad filename, ≥100 MiB, deleting `metadata.json`/`README.md`) and a `GET v=draft` check run **before** the first write. A `del` with no draft is a server panic, not an envelope. **The organisation key differs per endpoint and is mirrored**: commands use body `organisation_id`, upload uses query `o`, `del` uses body `organisation`. Upload `filename`/`checksum` must precede the `file` part (requests encodes `data` before `files`). `success:false` on HTTP 200 raises `DatasetCommandError`, except `del`'s `Path '…' not found`, which counts as deleted. **Publish's `comment` is not stored server-side.** `draft`/`publish` patch the local `dataset.json` (status/version) when given a matching `root`. See `docs/datasets.md`
- `datasource_client.py` / `datasource_dto.py` — datasource + table introspection
- `files_client.py` / `files_dto.py` — tenant file storage: list/upload/folders/delete, signed URLs, `gtsf://` resolution, streamed download (see `docs/files_storage_flows.md`)
- `ontology_client.py` / `ontology_dto.py` — list (`GET /api/ontologies`, optional `kind`) so a caller can resolve a name → id; export (`json`, `owl`/`turtle`, `shacl`, `shacl-package`, `package`, `package-zip`) + typed `OntologySnapshot` (`export_snapshot`) + entity-datasource mappings. Zip is binary: `export()` raises, `export_bytes()` returns `bytes`.
- `prompts_client.py` + `domain.py` — prompt CRUD via command pattern (`POST /_command`)
- `rules_client.py` / `rules_dto.py` — rule listing, detail, execution + execution history
- `session_vault_client.py` / `session_vault_dto.py` — session-scoped key/value vault for agent state (classification-based encryption, audited reveal). Agent Vault additions (all additive): per-key monotonic `version`; `set(if_version=)` → 412 / `set(if_absent=True)` → 409, both raised as `ConditionalWriteFailed` (control flow, an `HTTPError` subclass; conditional writes opt into connection-retry, so a lost precondition after a retry may be the caller's own first attempt — re-read to tell); `list(prefix=, page=, size=)`; `scope="app"|"user"` on every call, **resolved server-side from the session** (user = bound owner, refused 4xx when unbound) so the SDK never names a user; per-call `timeout=`
- `work_queue_client.py` / `work_queue_dto.py` — work-queue ingestion (`/api/work_queues`): create-item-with-external-refs, publish, `add_ref`, **queue-scoped owner lookup** (`get_items_by_ref` — a ref may be held once per queue; runs over the paginated `/items?ref_type=&ref_value=`, `_` = org scope), unblock/retry/reprocess commands, item delete, and `list(org)` (queue name→id). **409 is control flow, not an error**: `create_item`/`add_ref` catch the `HTTPError` and return typed claim-lost results — the owner summary comes from the standard error envelope's `error.details`, with an additive `contended` flag for the owner-less `CONFLICT_RETRY` fallback. Mirrors the gts-agency Track ① contract (guideline-agent `docs/dbq/files-inbox-ingestion-design-20260712.md` §5/§6)
- `session_client.py` / `session_dto.py` — report progress on a **dispatched** session (`/api/sessions/{id}/_command`): `attach(session_id)` binds the inherited session (no HTTP), `update(...)` posts the `{command:"update", organisation, update:{status,...}}` envelope. **Never `register`s** — the agent inherits the session id the ① worker injects, so self-registering would mint an orphan session (registration lives on the templates delegate). Agent Vault reads: `find_by_correlation_id` (`POST /_lookup`, id in the body; a miss and a foreign-org hit are both `None`) and label `search` (`POST /_search`); `update(labels=)`. The SDK marshals a caller-decided `SessionStatus` (-1/0/2, plus provisional `AWAITING_INPUT`=3, set by comand when a HITL request is created); it never infers the outcome. `AnalyticsEvent` is the promoted cross-agent event shape. Design: `docs/session_reporting_delegate_design.md`
- `annotations_client.py` / `annotations_dto.py` — publish a knowledge graph as annotator work (`/api/annotations` + `/api/annotation-specs` + `/api/annotation-workflows`, via `AgencyClient.annotations()`). **Publishing is three calls, not one**: `create_batch` (`POST /_command`, standard `{success,message,data:{id}}` envelope) leaves the batch in DRAFT with 0 jobs; `bind_workflow` attaches the workflow that governs its jobs — **without it the server refuses every job insert and the upload 500s** (the batch is created governed by nothing; issue #14); `upload_graph` (multipart `file`, 50 MiB cap) then materialises **one job per vertex matching `target_class`** (default `rule`) and flips the batch to ACTIVE — and its response body is `null`, so the job count comes from a `get_batch` read-back. `push_graph` chains resolve-workflow → create → bind → upload → read-back and is the agent-facing one-liner; the graph is the same `create.graph` payload sent to the ontology sandbox, accepted as a dict or a file path. Workflow ids are **resolved via `list_workflows`, never hardcoded** (system workflows are seeded per org); pass `workflow_id=` to override, and a 404 from that endpoint means a pre-workflow server, where the bind is skipped. Also `create_spec`/`get_spec`/`list_specs`, which seed the checklist a `job_type`'s jobs start from (**`get_spec`'s path segment is the `code`**, not the UUID). Batch job counters differ by server generation (`resolved`/`accepted`/`rejected_jobs` now, `completed_jobs` on older ones) so all four are optional. A failed upload leaves an empty DRAFT batch behind by design. **Reading the work back is four read-only methods**: `list_jobs` (paged **summaries** — the server excludes the heavy payloads via a deferred join, so `vertex_data`/`connected_*`/`delta`/`checklist_state`/`annotation_data` exist only on the single read), `get_job` (full row incl. `revision`, the fence against post-approval drift), `list_job_transitions` (approval evidence; `prior_actor_conflict` is a stop signal, and **`note` is PHI-capable — never log or trace it**), and `get_graph` (the upload echoed back as a raw `dict`, deliberately unmodelled). **Query-param names differ per endpoint and are mirrored, not normalised**: jobs use `o`/`p`/`s`, transitions use `organisation`/`page`/`size` (the server binds `OrganisationQuery` there). `list_job_transitions` is additionally **clamped to `size` 500 server-side, silently** (`annotation_transition_service.rs`), so a walk ending on `returned < requested` misreads the clamp as the last page. The edit columns are opaque `Any` — comand does whole-value replacement and two front-ends write different shapes into them. No write path: a consumer of annotation output never writes back (issue #16). **All five list reads validate rows individually** (`_parse_page` + `TolerantPage`): a bad row lands in `rejected` (index/raw/error) instead of losing the whole page — one out-of-scope malformed row used to wedge a consumer's sweep permanently, since callers filter scope after parsing. Rejects are reported, never silently dropped; the `page` envelope and the single-item reads still fail hard. **Page on `TolerantPage.row_count`** (`items` + `rejected`), never `len(items)` — tolerance broke the old invariant that `len(items)` was the server's row count, so the short-page idiom truncates a walk silently and on a transition ledger flips a separation-of-duties gate from fail-closed to fail-open. **This delegate only** — the SDK's other paged reads keep whole-page validation. `list_batch_members` (`GET {batch_id}/members`, `o`/`p`/`s`, paged despite an OpenAPI annotation claiming a bare `Vec`) is the server's record of **who held which role and when**: rows are **role-per-row** (key `(batch_id, user_id, role, eff_from)`, so one person holding three roles is three rows), `role` is an **open vocabulary** (`member`/`admin` plus workflow-declared codes like `annotator`/`reviewer`/`approver` — no enum), audit fields are flat, and the display fields are **PII**. Read role windows from here, not from the ledger. Also: a `RESTRICTED` batch the caller is not an active member of is **absent from `list_batches`** entirely (server-side filter), not rejected. See `docs/annotations.md`
- `session_templates_client.py` / `session_templates_dto.py` — list an org's session templates (`GET /api/session_templates`), via `AgencyClient.session_templates().list(org)`, so a caller can resolve a template **name → id** at runtime (the guideline-agent dispatcher wires queue/template by name from `schedule.static_input` instead of hardcoding ids); `resolve_id`, and `register(org, name, *, correlation_id, labels, input, owner)` — the correlation id is a label comand keeps unique per org, so a duplicate raises `CorrelationIdConflict` carrying the existing session id
- `session_hitl_client.py` / `session_hitl_dto.py` — human-input requests (`/api/sessions/{id}/hitl`), via `AgencyClient.session_hitl()`. The agent `create`s (idempotent on `request_id`, sets the session `AWAITING_INPUT`), `get`s and `list`s; **`respond` must be called by a client built with the human's own token** — comand rejects service principals (403 `HitlNotAuthorised`), keeps the first answer (409 `HitlAlreadyAnswered`), refuses expired requests (410 `HitlRequestExpired`, expiry is comand's background job) and records the verified `responder`. `prompt`/`response` are PHI-capable and hidden from `repr`
- `gateway_client.py` / `gateway_dto.py` — LLM calls through the org's agentgateway, **openai-SDK-only**. `AgencyClient.gateway(*, org_id, gateway_base_url=None, environment=None)` (DCL-cached per `(org_id, gateway_base_url/environment)` identity; URL and environment are mutually exclusive) returns an `AgencyGatewayClient` that is a thin **factory**, not an HTTP client: `openai_client()`/`async_openai_client()` return standard `openai.OpenAI`/`AsyncOpenAI` wired to the gateway host (`/v1`), with the `x-org` routing header (not `x-org-id`) and a per-request rotating-bearer httpx auth hook (from `agency_sdk/auth_hooks.py`; the construction-time `api_key` is a placeholder). `openai` is a **core dependency**. `gateway_dto.py` holds only the discovery DTOs (`AgentGatewayStatusResponse`, `extra="allow"`); omitting `gateway_base_url` discovers the URL via `GET /api/agentgateways?o={org}` (Page-wrapped response; live-verified 2026-07-07). See `docs/gateway.md`, design in `docs/gateway_design.md`

**DTOs:** All models use Pydantic v2 `BaseModel`. Datasource, ontology mappings, and rules DTOs use `ConfigDict(alias_generator=_to_camel, populate_by_name=True)` for camelCase JSON mapping. Ontology list items (`Ontology`) and *export snapshots* (`OntologySnapshot` and nested entity/relation/property models) are snake_case — that is how the Control Plane serialises them. Prompt/dataset/files DTOs use snake_case matching the API.

**Shared type:** `Page` is defined in `datasets_dto.py` and imported by other DTO modules for pagination.

**Observability (optional):** `agency_sdk/observability/` (`bootstrap.py` + `auth.py`) adds OTLP tracing/logging to a Langfuse backend, reached via `AgencyClient.observability(...)`. Heavy OpenTelemetry/Langfuse imports are deferred to the lifecycle methods so importing the SDK never pulls them. The per-request bearer hooks (`auth.py`) and exporters reuse the shared `CredentialsSupplier`. Use `obs.init()` then `with obs.agent_run(name, **attrs):` (see `docs/observability.md`, design in `docs/observability_design.md`).

**ADK sessions (optional `[adk]`):** `agency_sdk/adk/` — `GtsSessionService` (ADK 2 `BaseSessionService` on the session vault; one ADK session = one GTS session) and `DurableRunner` (correlation-id runs that resume from the last checkpoint; HITL answered by an authenticated human in comand). `VaultStore` (`_store.py`) is the **only** async↔sync seam (`asyncio.to_thread`, 10 s timeout, URL-free `VaultUnavailable`); `_records.py` is the key scheme + codec. Load-bearing rules: the conditional head write is the **sole commit point** (event `if_absent`, then head `if_version`; an unlinked event is an ignored orphan); events load via **`model_validate_json`** (ADK bytes are base64 only in JSON mode); stale writers raise ADK's `StaleSessionError` having mutated nothing; `temp:` is never stored; record/DTO `repr`s hide PHI; `adk.failure` holds the exception **type**, never its message. A `LongRunningFunctionTool` pauses only when its function returns `None`. The comand endpoints follow the contract in `docs/agent_vault_gap_analysis.md` §4; offline fakes of it live in `agency_sdk/test/adk_fakes.py` (file-backed for the multi-process tests), and `pytest -m integration` runs the opt-in suite against a local comand. See `docs/adk_sessions.md`

**Tests:** Offline suite in `agency_sdk/test/`; `conftest.py` stubs `requests` via monkeypatch so no test touches the network. mypy is relaxed for `agency_sdk.test.*` per pyproject overrides. End-to-end verification against the local stack: `docs/local_e2e.md`.

## Conventions

- Use `dict | None` syntax (PEP 604), not `Optional[Dict]` — Python 3.12+ only
- Pydantic v2 API: `model_dump(mode="json")`, `ConfigDict`, `Field`
- HTTP errors propagate via `response.raise_for_status()` — no custom exception wrapping
- API query params use abbreviations: `o` (org), `s` (size), `p` (page), `v` (version) — but **mirror the endpoint, not this rule**, when the server disagrees (annotation job transitions bind `organisation`/`page`/`size`)
- Line length: 120 characters (black config)
- mypy strict mode for all production code; tests excluded

## CI/CD

`.github/workflows/publish.yaml` — triggered by `v*` tags. Builds, publishes to PyPI via trusted publisher (OIDC), notifies Slack.