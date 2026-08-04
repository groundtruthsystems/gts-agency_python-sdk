# Spec: Annotations delegate client — push graph batches to the annotators (→ rc14)

## Overview

`gts-guideline-agent` issue
[#22](https://github.com/groundtruthsystems/gts-guideline-agent/issues/22) needs the extracted
rule graph to reach human annotators, not just the ontology sandbox. Publishing is a **two-step
control-plane flow** with no single "publish" endpoint, and the graph it accepts is **byte-for-byte
the `create.graph` payload ③ already builds** for `sandbox_command.json` (`run_id` / `vertices` /
`edges`, rule vertices carrying `class: "rule"`). So the SDK side is plumbing — and the delegate's
job is to make ③'s integration a one-liner.

This track adds `AgencyAnnotationsClient` (`/api/annotations`) plus
`AgencyClient.annotations()`, covering the push path and the read-back that proves it worked:

1. `create_batch` — `POST /api/annotations/_command` (`command: "create"`) → batch id, **DRAFT**,
   `total_jobs = 0`.
2. `upload_graph` — `POST /api/annotations/{batch_id}/upload` (multipart) → the server materializes
   **one job per vertex whose `class` matches `target_class`**, attaches each vertex's N-hop
   neighbourhood as context, stores the raw file at `{org}/annotations/{batch_id}/graph.json`, and
   flips the batch to **ACTIVE** with `total_jobs` set.
3. `get_batch` / `list_batches` — read back `total_jobs` / `status`, and resolve a batch by name.
4. `push_graph` — the one-call convenience that runs 1 → 2 → 3 and returns
   `{batch_id, total_jobs, status}`. This is ③'s integration point, and it mirrors the reference
   client (`gts-comand` CLI `annotations push`) step for step.

Plus the **job specifications** (`/api/annotation-specs`) that decide whether those jobs reach the
annotators with a usable checklist: on upload the server seeds each job's `checklist_state` from
the spec whose `code` equals the upload's `job_type` (default `rule_validation`), and silently
leaves the checklist empty when no such spec exists. Seeding that spec is therefore part of making
a pushed batch actually reviewable, so the delegate covers `create_spec` / `get_spec` / `list_specs`
as well.

The SDK is a **consumer** of an existing, unchanged control-plane contract: no gts-comand work is
in scope (repo boundaries), and neither is ③'s pipeline wiring
(`guideline_agent/workflows/adk_pipeline.py` / `adk_tools.py`) — that is ③'s own track.

### Verified contract (read from `gts-comand` at `8d64a64a`)

| Step | Endpoint | Notes |
|---|---|---|
| create | `POST /api/annotations/_command` | Body `{command:"create", organisation:<int>, payload:{name, description?, instructions?, batch_type?, confidentiality_level?}}`. `create` is the **only** command the batch command handler accepts. Response is the standard `CommandResponse` **envelope** `{success, message, data:{id}}` — *not* a bare `{id}`. `batch_type` defaults to `"graph"`; `confidentiality_level` defaults to `INTERNAL` (only `INTERNAL` / `RESTRICTED` are accepted; `RESTRICTED` gates the batch behind membership and seeds the creator as admin). |
| upload | `POST /api/annotations/{batch_id}/upload` | `multipart/form-data`, single field **`file`**; query `o` (required), `job_type` (default `rule_validation`), `target_class` (default `rule`), `hops` (default `1`); 50 MiB body limit. Response body is **`null`** (`Json<()>`) — the job count must be read back. 400 when the batch is not DRAFT, when the JSON will not parse, when `vertices` is missing, or when **no vertex matches `target_class`**. |
| read back | `GET /api/annotations/{batch_id}?o=` | `AnnotationBatchResponse` = the batch fields (incl. `total_jobs`, `completed_jobs`, `status`, `graph_uri`, `graph_run_id`, `target_class`, `context_hops`, `audit_data`) **flattened** alongside `viewer_role`. |
| list | `GET /api/annotations?o=&p=&s=&batch_type=&view=` | Page-wrapped `{page, items}`; server default size 10. |
| create spec | `POST /api/annotation-specs/_command` | Body `{command:"create", organisation, payload:{code, name, checklist, instructions?}}`; `create` is the only command this handler accepts. `checklist` is a JSON array whose items carry an `id`; upload seeds `{item_id: false}` for each. Created **ACTIVE** (`status = 1`). Response is the same `{success, message, data:{id}}` envelope. |
| get spec | `GET /api/annotation-specs/{code}?o=` | **Gotcha:** the OpenAPI path calls the segment `{id}`, but the service resolves it with `get_by_code` — the segment is the spec **`code`** (e.g. `rule_validation`), not the UUID. 404 when absent. |
| list specs | `GET /api/annotation-specs?o=&p=&s=` | Page-wrapped `{page, items}` of `AnnotationJobSpecification` {id, organisation_id, code, name, instructions?, checklist, status, audit_data}; server default size 10. |

Batch `status` is an integer: `0` DRAFT, `1` ACTIVE, `2` COMPLETED, `3` ARCHIVED. Spec `status`:
`0` DRAFT, `1` ACTIVE, `2` ARCHIVED.

## Functional Requirements

- **FR1 — delegate + facade.** New `agency_sdk/delegates/annotations_client.py`
  (`AgencyAnnotationsClient`, `api_path = "/api/annotations"`) and
  `annotations_dto.py`, wired as `AgencyClient.annotations()` (eager, like the other delegates —
  `self.annotations_client` built in `__init__`, sharing the `CredentialsSupplier`).

- **FR2 — `create_batch`.**
  `create_batch(organisation_id, *, name, description=None, instructions=None, batch_type="graph",
  confidentiality_level=None) -> CreateBatchResult`.
  Posts the `{command, organisation, payload}` envelope; optional payload fields are **omitted when
  `None`** so the server's own defaults apply. Parses the `CommandResponse` envelope and surfaces
  `data.id` as `CreateBatchResult.id`; raises `ValueError` if a 2xx response carries no id (the
  reference client treats this the same way).

- **FR3 — `upload_graph` (dict *or* file path).**
  `upload_graph(organisation_id, batch_id, *, graph=None, file_path=None, job_type=None,
  target_class=None, hops=None, filename="graph.json") -> None`.
  - Exactly one of `graph` (a `Mapping`, serialized with `json.dumps`) or `file_path` (bytes read
    from disk) must be given — otherwise `ValueError` **before any network call**.
  - Sends `files={"file": (filename, payload, "application/json")}` with a bearer header, mirroring
    `files_client.upload` (multipart must not carry the JSON content-type header, so it bypasses
    `_make_request` and calls `requests.post` directly, `timeout=300`).
  - `job_type` / `target_class` / `hops` are sent **only when not `None`**, leaving the documented
    server defaults authoritative.
  - Returns `None` (the endpoint's body is `null`); errors propagate via `raise_for_status`.

- **FR4 — reads.**
  - `get_batch(organisation_id, batch_id) -> AnnotationBatchResponse`.
  - `list_batches(organisation_id, *, page=0, size=50, batch_type=None, view=None) ->
    AnnotationBatchesPagedResult` (`Page` + items), so a caller can find a batch by name or list the
    org's batches.

- **FR5 — `push_graph`, ③'s one-liner.**
  `push_graph(organisation_id, *, name, graph=None, file_path=None, description=None,
  instructions=None, confidentiality_level=None, job_type=None, target_class=None, hops=None) ->
  PushGraphResult` runs create → upload → get and returns
  `PushGraphResult(batch_id, total_jobs, status, batch)`. Input validation (exactly one graph
  source) happens **before** the batch is created, so a bad call cannot leave a stray batch.
  Documented failure semantics: if the upload leg fails (e.g. no `rule` vertices → 400), the
  `HTTPError` propagates and the **empty DRAFT batch survives** server-side; it holds no jobs and is
  findable via `list_batches`. The SDK does not auto-archive it — deleting/archiving on the client's
  behalf is a side effect the caller did not ask for.

- **FR6 — job specifications (checklist seeding).**
  - `create_spec(organisation_id, *, code, name, checklist, instructions=None) -> CreateSpecResult`
    — posts the `{command:"create", organisation, payload}` envelope to
    `/api/annotation-specs/_command` and returns `data.id`, same envelope handling as FR2.
    `checklist` is passed through as given (a list of `{"id": ..., ...}` items).
  - `get_spec(organisation_id, code) -> AnnotationSpec` — `GET /api/annotation-specs/{code}?o=`.
    The docstring must state that the path segment is the **code**, not the id (the server's own
    path parameter is misleadingly named `id`), and that a missing spec is a 404 `HTTPError`.
  - `list_specs(organisation_id, *, page=0, size=50) -> AnnotationSpecsPagedResult`.
  - No `ensure_spec` convenience: get-then-create is a two-call race the SDK should not hide. The
    recipe (get → 404 → create, before pushing) goes in `docs/annotations.md`, where it belongs as
    a documented one-time org setup step.

- **FR7 — DTOs** (`annotations_dto.py`, snake_case matching the API, Pydantic v2):
  `AnnotationBatch` (id, organisation_id, name, description?, instructions?, batch_type, graph_uri?,
  graph_run_id?, target_class?, context_hops?, total_jobs, completed_jobs, status,
  confidentiality_level, audit_data), `AnnotationBatchResponse` (the batch fields + `viewer_role?` —
  the server flattens them into one object), `AnnotationBatchesPagedResult` (`Page` + items),
  `CreateBatchResult` (success, message, id), `PushGraphResult` (batch_id, total_jobs, status,
  batch), `AnnotationSpec` (id, organisation_id, code, name, instructions?, checklist, status,
  audit_data), `AnnotationSpecsPagedResult`, `CreateSpecResult`, and two `IntEnum`s following the
  `SessionStatus` precedent — `BatchStatus` (`DRAFT=0, ACTIVE=1, COMPLETED=2, ARCHIVED=3`) and
  `SpecStatus` (`DRAFT=0, ACTIVE=1, ARCHIVED=2`). Batch-type / job-type / target-class defaults are
  exposed as module constants (`BATCH_TYPE_GRAPH`, `DEFAULT_JOB_TYPE = "rule_validation"`,
  `DEFAULT_TARGET_CLASS = "rule"`) for documentation, not baked into the request when the caller
  omits them.

- **FR8 — docs & example.** `examples/quick_annotations.py` (self-verifying, unique batch name,
  asserts `total_jobs == number of rule vertices`, prints PASS per step, non-zero exit on failure),
  `docs/annotations.md` (flow, the two-step contract, ③'s integration snippet, the DRAFT/ACTIVE
  lifecycle, the spec-seeding recipe and the code-vs-id gotcha), plus `README.md` and `CLAUDE.md`
  delegate entries.

- **FR9 — release.** Bump `0.0.1rc13 → 0.0.1rc14`, merge to `main`, tag `v0.0.1rc14`, publish to
  PyPI via the existing OIDC workflow, so ③ can pin a released version instead of an editable link.

## Non-Functional Requirements

- **TDD, offline:** every method covered by stubbed tests (`conftest.py` monkeypatches `requests`),
  including the client-side `ValueError` paths and the multipart field/params shape. Target 100%
  coverage on the two new modules, per the precedent of the recent delegates.
- **mypy strict / black (120 cols) / bandit** clean; no behaviour change to any existing delegate.
- **Memory:** `requests` assembles the whole multipart body in memory, and the server caps the body
  at 50 MiB — documented on `upload_graph`, matching the note on `files_client.upload`.
- **No custom exception wrapping:** HTTP errors propagate via `raise_for_status()`. Unlike the
  work-queue delegate, no status code here is control flow.

## Risks & open questions (from issue #22)

- **R1 — m2m principal (blocking for the live gate, not for the code).** Every annotations handler
  rejects a caller without a local user id (`annotations.rs:557`: "User not supplied.") and requires
  org-level **write on `Resource::Annotations`**. Whether the guideline-agent's OAuth2
  client-credentials principal satisfies both is unverified; Phase 3 verifies it live and, if it
  fails, the finding is reported (provisioning the grant is control-plane/ops work, out of scope
  here).
- **R2 — `rule_validation` spec provisioning (addressed).** A job's `checklist_state` is seeded only
  when an `annotation_spec` whose `code` matches `job_type` exists for the org; without one, upload
  still succeeds and every job gets an empty checklist. FR6 gives the SDK the surface to seed it,
  and the e2e exercises both orders (spec-then-push seeds the checklist, push-without-spec leaves it
  empty), so the empty case is a documented outcome rather than a mystery. *Who* runs the seeding
  per org remains an ops/③ decision, not an SDK one.
- **R3 — duplicate spec codes.** Nothing server-side enforces `code` uniqueness per org, and the
  lookup takes the first matching row. The docs therefore present seeding as get-then-create rather
  than create-blindly; the SDK does not attempt to detect or repair duplicates.

## Acceptance Criteria

- **Unit (CI, stubbed):** `create_batch` posts the exact `{command:"create", organisation, payload}`
  envelope with `None` fields omitted and returns `data.id`; a 2xx with no id raises `ValueError`;
  `upload_graph` sends the `file` multipart field with the right filename/content-type, passes only
  the supplied query params, and accepts a dict **and** a path (byte-identical body); passing both
  or neither raises `ValueError` with no HTTP call; `get_batch` / `list_batches` parse their
  responses (incl. `viewer_role` and the paged envelope); `push_graph` performs create → upload →
  get in order and returns the read-back `total_jobs`; `client.annotations()` returns the delegate.
  `create_spec` posts its envelope and returns `data.id`; `get_spec` hits
  `/api/annotation-specs/{code}` (code in the path, `o` in the query) and a 404 propagates;
  `list_specs` parses the paged envelope.
- **Live e2e (Gate A):** `python examples/quick_annotations.py` against a real control plane seeds
  (or finds) a `rule_validation` spec, creates a batch, uploads a small rule graph, and reads back
  `status == ACTIVE` with `total_jobs` equal to the number of `class: "rule"` vertices; the negative
  case (graph with no rule vertices) returns 400. R1 confirmed or reported. Note the script asserts
  the spec exists **before** the upload, not that a job's `checklist_state` came out seeded —
  reading jobs is outside this delegate's surface (see Out of Scope), so that link is verified in
  the annotation UI, not by the script.
- **rc14 published** to PyPI and installable.

## Out of Scope

- Any gts-comand change (the API is used as-is).
- ③'s pipeline integration itself — the new ADK tool/step in `adk_pipeline.py` / `adk_tools.py` is
  guideline-agent work; this track only guarantees the SDK surface it calls.
- `upload-dataset` (dataset batches), job reads/updates (`/jobs`, `/jobs/{job_id}/_command`), the
  batch graph read-back (`/{batch_id}/graph`), batch member management, the audit log, and the
  `archive` / `unarchive` / `set_confidentiality` batch commands. The delegate is deliberately the
  **push path, its specs, and the read-back that proves the push**; the rest can be added when a
  consumer needs it.
- `update_spec` (`POST /api/annotation-specs/{id}/_command`, command `update`). Unlike the read, its
  path segment really is the spec **UUID** — that asymmetry deserves its own tests and no consumer
  needs it yet, so seeding is create-only for now.
- Retry/resume semantics for a partially failed push (documented, not automated).

## Branch

`claude/annotations-sdk-client-1b1a26` (worktree `cranky-moser-96d783`), created from `main`
(at `a005382`).
