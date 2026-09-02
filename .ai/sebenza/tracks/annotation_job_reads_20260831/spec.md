# Spec — Annotations: four read-only job delegates for the annotation→KG Publisher

**Track:** `annotation_job_reads_20260831`
**Type:** Chore (additive; no existing behaviour changes)
**Branch:** `feat/annotation-job-reads`
**Issue:** [gts-agency_python-sdk#16](https://github.com/groundtruthsystems/gts-agency_python-sdk/issues/16)
**Design of record:** `gts-guideline-agent` `docs/dbq/annotation-publisher-transfer-design-20260829.md` §2.2 (this repo is its Track **B1**)
**Consuming track:** `gts-guideline-agent` `annotation_kg_publisher_20260831`

## Overview

`AgencyAnnotationsClient` (rc15) covers batches, workflows and specs — everything needed to *write*
annotation work. The Publisher session in guideline-agent has to *read it back*: a completed batch's
jobs, the human edits on each one, and the transition ledger that proves who approved what. None of
those four reads exist in the SDK, and the consuming repo forbids bespoke control-plane clients, so
they have to land here first.

This track adds exactly four read-only methods and their DTOs. **Zero comand changes** — all four
endpoints already exist and are verified in-tree at gts-comand `90f95ad8`
(`crates/comand/src/handler/annotations.rs`, `model/annotation.rs`, `model/annotation_workflow.rs`).

Two properties of the server drive most of the requirements below:

1. **Query-param naming is inconsistent across these four endpoints.** The jobs list and single-job
   read take `o`/`p`/`s`; the transitions read takes `organisation`/`page`/`size`. That is the wire
   contract, not a bug to normalise — each method mirrors its own endpoint.
2. **Three job columns are opaque blobs.** comand never parses `annotation_data`, `checklist_state`
   or `delta`; the update arm does whole-value column replacement and their inner shape is owned by
   whichever front-end wrote them (the DBQ app and comand-web's `JobVerificationPage` write
   *different* structures into the same columns). Modelling them would break on shape evolution.

## Functional Requirements

### FR1 — `list_jobs(organisation_id, batch_id, *, page=0, size=50)`

- Wraps `GET /api/annotations/{batch_id}/jobs?o=&p=&s=`.
- Returns `AnnotationJobsPagedResult` (`page: Page`, `items: list[AnnotationJobSummary]`).
- Summaries only. The server deliberately excludes the heavy payloads (`vertex_data`,
  `connected_vertices`, `connected_edges`, `delta`, `data`, `checklist_state`, `annotation_data`) —
  a list page never renders them, and the deferred join that produces the page exists precisely to
  keep those wide rows out of it. The docstring must state that a caller needing any of them must
  follow up with `get_job`.
- Param names `o`/`p`/`s`, sent as strings, matching every other list read in this delegate.

### FR2 — `get_job(organisation_id, batch_id, job_id)`

- Wraps `GET /api/annotations/{batch_id}/jobs/{job_id}?o=`.
- Returns `AnnotationJob` — the full row.
- Param name `o` only; this endpoint takes no pagination.

### FR3 — `list_job_transitions(organisation_id, batch_id, job_id, *, page=0, size=50)`

- Wraps `GET /api/annotations/{batch_id}/jobs/{job_id}/transitions?organisation=&page=&size=`.
- Returns `JobTransitionsPagedResult` (`page: Page`, `items: list[JobTransitionEntry]`).
- **Param names are `organisation` / `page` / `size`** — spelled out, unlike every other method on
  this client. A test must assert this explicitly so a future "consistency" refactor cannot silently
  break it.
- The ledger is the approval evidence: `transition_code`, `from_state`/`to_state`, `actor_user_id`,
  `acting_as_role`, `prior_actor_conflict`, and the `workflow_version_id` **in force when the
  transition fired** (not the job's current one — bindings move, and only the stamped copy makes a
  past transition attributable to the policy that permitted it).

### FR4 — `get_graph(organisation_id, batch_id) -> dict[str, Any]`

- Wraps `GET /api/annotations/{batch_id}/graph?o=`.
- Returns the originally uploaded graph JSON as a **raw `dict`**, deliberately unmodelled: it is
  whatever the caller uploaded, echoed back, and the SDK has no business constraining it. Callers use
  it as a pristine cross-check copy against the per-job `vertex_data`.
- The server 400s if the stored file does not parse as JSON.

### FR5 — DTOs (`agency_sdk/delegates/annotations_dto.py`)

Modelled against the **full server structs**, not the abbreviated table in the issue. Pydantic
silently drops undeclared fields, so an under-specified model would quietly discard data the
consumer needs — `display` and `audit_data.modified_on` are both load-bearing in the Publisher's
merge and drift fence.

**`AnnotationJobSummary`** — `id`, `batch_id`, `organisation_id`, `job_type`, `vertex_bid | None`,
`vertex_name | None`, `display | None`, `state_code`, `workflow_version_id`, `audit_data`.

**`AnnotationJob`** — everything in the summary plus the payloads: `vertex_data`,
`connected_vertices`, `connected_edges`, `delta` (graph subtype), `data` (dataset subtype),
`checklist_state`, `annotation_data`, and `revision`.

- Modelled as a **standalone class, not a subclass of the summary**. The two are different server
  structs that happen to overlap; expressing the full row as an extension of the list row would
  claim a substitutability the API does not promise (contrast `AnnotationBatchResponse`, which
  *is* the batch plus one field and correctly inherits).
- `revision` is the optimistic-concurrency counter, bumped by every content write. The consuming
  Publisher snapshots it per job and re-reads after assembly to detect post-approval drift, so its
  docstring must say what it is for rather than just what it is.

**`JobTransitionEntry`** — `id` (`int`), `job_id`, `batch_id`, `organisation_id`,
`workflow_version_id`, `from_state | None`, `to_state`, `transition_code`, `actor_type`,
`actor_user_id | None`, `acting_as_role`, `note | None`, `prior_actor_conflict`, `occurred_on`.

**`AnnotationJobsPagedResult`** / **`JobTransitionsPagedResult`** — `page: Page` + `items`, matching
every other paged result in the SDK.

### FR6 — opaque blobs stay opaque

`annotation_data`, `checklist_state`, `delta`, `vertex_data`, `connected_vertices`,
`connected_edges` and `data` are typed `Any` (nullable) and pass through untouched. A test must
round-trip a deliberately unusual `annotation_data` shape to prove the SDK neither validates nor
reshapes it.

### FR7 — `state_code` is a string, not an enum

The server declares it `String`, and its values are declared by the job's governing **workflow
version** — they are workflow data, not an SDK-side constant. The consumer compares against
`"completed"` / `"skipped"`. No `IntEnum` (contrast `BatchStatus`/`SpecStatus`, which wrap genuine
server-side integer constants). The docstring names the two dispositions the Publisher acts on
without claiming the set is closed.

### FR8 — PHI note on `JobTransitionEntry.note`

`note` carries free-text skip/reject reasons written by clinicians and is **PHI-capable**. comand
deliberately excludes it from its own access log. Its docstring must state, in the field's own
documentation, that it is never to be logged, traced, or copied into telemetry.

### FR9 — read-only scope

No transition, claim, save or update methods. The endpoints exist (`POST .../jobs/{job_id}/_command`,
`GET .../jobs/{job_id}/actions`) and are deliberately **out of scope**: the Publisher never writes to
comand. Nothing in this track constructs a "completed batches" filter either — no server-side filter
exists and faking one client-side inside the SDK would hide the cost; callers filter
`status == BatchStatus.COMPLETED` over the existing `list_batches`.

### FR10 — docs and example

- `docs/annotations.md` gains a **"Reading a batch back"** section: the four methods, the
  summary-vs-full split, the param-name inconsistency, and the PHI warning.
- `examples/quick_annotations.py` is extended: after the existing `push_graph` leg (which has already
  materialised real jobs), list them, read one in full, read its transitions, and fetch the graph
  back — asserting the round-trip and keeping the script self-verifying and idempotent.
- `CLAUDE.md`'s annotations bullet is updated per the Delegate Delivery Checklist.

### FR11 — the annotations list reads tolerate a malformed row (added 2026-09-01)

**Added after the fact**, on the consumer's integration review (issue #16 follow-up comment) and
with the user's approval. The original spec put `list_batches` out of scope as "existing method,
unchanged"; that line no longer holds and has been corrected below.

Every list read on this delegate builds its page in one construction —
`AnnotationBatchesPagedResult(**body)` over `items: list[AnnotationBatch]` — so pydantic validates
the whole list and **one bad row loses the entire page**. Reproduced: a page of five where the third
row lacks `confidentiality_level` raises `ValidationError` and returns nothing; the four well-formed
rows are unreachable, not merely unreported.

The blast radius is what is wrong, not the strictness. The Publisher's sweep lists batches, then
filters `COMPLETED` and DBQ scope **client-side, after parsing** — so a malformed row it would have
discarded as out-of-scope wedges the sweep anyway, and keeps wedging it every six hours because the
row does not heal. A caller cannot work around it without abandoning the method and re-implementing
the paged read.

- The five list reads — `list_batches`, `list_jobs`, `list_specs`, `list_workflows`,
  `list_job_transitions` — validate items **individually**. Well-formed rows are returned.
- A row that fails is **reported, never silently dropped**, as a `RejectedRow` carrying its `index`
  within the page as the server sent it, the `raw` dict, and the validation `error`. Silent
  degradation is the specific failure the consumer's own review found to be worse than a crash
  (a degraded graph published while its closure check reported zero problems).
- The rejects are surfaced on the paged result as `rejected`, defaulted empty, so existing callers
  are source-compatible and a new caller cannot reach the good rows without the bad ones being in
  hand.
- A malformed `page` envelope still raises. That is a broken response, not a bad row.
- **Single-item reads keep failing hard** — `get_batch`, `get_job`, `get_spec`, `get_graph`. The
  boundary is principled rather than incidental: a discovery read killed by a row the caller was
  going to discard is disproportionate; a single read that cannot return the one thing you named
  has nothing useful to return.
- Scope is this delegate only. The SDK's other nine paged reads keep whole-page semantics; the
  resulting inconsistency is **documented rather than silent**, and extending the policy is a
  separate decision.

## Non-Functional Requirements

- `mypy agency_sdk/` strict passes; `black --check` at 120 chars; `bandit -r agency_sdk/ -x agency_sdk/test` clean.
- Coverage > 80% on the new code.
- Offline test suite only — no test touches the network (`conftest.py` stubs `requests`).
- `dict | None` / PEP 604 syntax; pydantic v2 API.
- No new dependencies.

## Acceptance Criteria

1. All four methods exist on `AgencyAnnotationsClient` with `organisation_id` positional-first,
   matching the delegate's existing signature convention.
2. Offline tests assert the **exact query-param names per endpoint** — `o`/`p`/`s` for FR1,
   `o` for FR2 and FR4, `organisation`/`page`/`size` for FR3 — and the exact URLs.
3. An opaque-blob round-trip test proves `annotation_data` survives unvalidated and unreshaped.
4. `get_graph` returns the uploaded dict unchanged (no DTO).
5. `AnnotationJob` parses a full server payload including every field in FR5 with nothing dropped.
6. `JobTransitionEntry.note`'s docstring carries the never-log instruction; `list_jobs`' docstring
   carries the summary-vs-full distinction.
7. `pytest`, `mypy`, `black --check`, `bandit` all green.
8. `examples/quick_annotations.py` runs green against the local stack, exercising all four reads on
   jobs the same run created.
9. `docs/annotations.md` and `CLAUDE.md` updated.
10. (FR11) A page with one unparseable row returns the well-formed rows and reports the bad one with
    its index, raw dict and error; a malformed `page` envelope still raises; the four single-item
    reads still raise; the policy and its deliberate limit to this delegate are documented.

## Out of Scope

- **Publishing rc16.** The track ends with the branch merge-ready and editable-installable. The
  version bump and PyPI tag wait on the consuming track's Gate A sign-off, which is not this repo's
  gate to call.
- **Any write path** — job commands, transitions, claims, checklist saves (FR9).
- **Whole-page tolerance for the SDK's other delegates.** FR11 covers this delegate's five list
  reads only; `datasets`, `files`, `datasource`, `ontology`, `prompts`, `rules`, `session_templates`
  and `work_queues` keep whole-page validation. Changing them is a separate decision — this PR
  should not quietly rewrite nine surfaces nobody reviewed.
- **The Publisher's own logic** — the merge, the contamination check, the unresolved-variables gate,
  the drift fence. Those are the consuming track's; this repo supplies the reads they run on.
- **The ontology-service `_publish` call**, which goes through guideline-agent's own `OntologyClient`.
- **Any comand change.** All four endpoints already exist.
- **Environment/stack setup** for the E2E leg — that belongs to `gts-local-environment`.
