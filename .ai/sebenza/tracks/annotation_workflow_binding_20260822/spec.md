# Spec: push_graph must bind a workflow, and read back the batch the server actually sends

Fixes [#14](https://github.com/groundtruthsystems/gts-agency_python-sdk/issues/14). Two independent
defects make `push_graph` unusable against a current control plane; both are confirmed against
`gts-comand` `eda4f9ca` and reproduced live.

## Defect 1 — no workflow binding, so no job can be inserted

comand migration `95__annotation_state_constraints.sql` adds `annotation_job_before_insert`, which
resolves a job's `workflow_version_id` from `annotation_batch_workflow` on `(batch_id, job_type)`
then `(batch_id, '*')` and raises `SIGNAL SQLSTATE '45000'` when neither exists. Migration `94`
backfilled one `'*'` binding per **pre-existing** batch only; `create_batch` (read at `eda4f9ca`)
writes the batch, an optional member row and an access-log row and **never touches
`annotation_batch_workflow`**. So every batch created through the API since that migration cannot
receive jobs: `upload_graph` returns an opaque `500 SERVICE_ERROR` and leaves an empty DRAFT batch.

`push_graph` (create → upload → read back) has no seam for the binding, so the fused call is dead
against any current server.

## Defect 2 — the read-back model no longer matches the server

`AnnotationBatch.completed_jobs` is required, but the field is **gone**. comand split it:

> "Split of `resolved_jobs` by outcome. 'Done' and 'done well' are different questions, and the
> single completed_jobs counter conflated them."

A live batch read returns `total_jobs`, `resolved_jobs`, `accepted_jobs`, `rejected_jobs` (plus
`viewer_role` on the single-batch read) and no `completed_jobs`, so pydantic raises
`1 validation error … completed_jobs Field required` **after** a fully successful publish — a caller
cannot tell that from a real failure. Note this is wider than the issue reports: the field was not
merely dropped, it was replaced by three counters.

## Functional Requirements

- **FR1 — `bind_workflow`.** `bind_workflow(organisation_id, batch_id, *, workflow_id, job_type="*",
  rebind_reason=None) -> BindWorkflowResult`, posting
  `{command:"bind_workflow", organisation, payload:{job_type, workflow_id, rebind_reason?}}` to
  `/api/annotations/{batch_id}/_command`. The response envelope carries
  `data:{workflow_version_id, jobs_regoverned}`. Worth having on its own: callers hitting this today
  hand-roll it with raw `requests` (there is such a workaround in gts-guideline-agent).
- **FR2 — `list_workflows`.** `list_workflows(organisation_id, *, page=0, size=50) ->
  AnnotationWorkflowsPagedResult` over `GET /api/annotation-workflows?o=&p=&s=`, so a workflow id is
  **resolved, never hardcoded** — `sys-wf-graph-2` is seeded per organisation.
- **FR3 — `push_graph` gains the binding leg**, in this order: resolve workflow → `create_batch` →
  `bind_workflow` → `upload_graph` → `get_batch`. Resolution happens **before** the batch is created,
  so a resolution failure cannot leave an orphan DRAFT batch.
  - `workflow_id=` given → used as-is, no lookup.
  - Otherwise pick from `list_workflows` the entries whose `target_batch_type` equals the batch type
    and that have a `current_published_version_id` (binding resolves the *published* version
    server-side, so an unpublished workflow cannot be bound), preferring `is_system`, then server
    order. Ambiguity is not an error — a caller with several graph workflows passes `workflow_id`.
  - No suitable workflow → `ValueError` naming the batch type and what was found, raised before any
    server state exists.
  - **Old control planes:** a `404` from `GET /api/annotation-workflows` means a server that predates
    workflows, where no binding is needed; the bind leg is skipped rather than failing. Any other
    error propagates.
- **FR4 — batch counters follow the server.** `completed_jobs` becomes optional (pre-split servers
  only) and `resolved_jobs` / `accepted_jobs` / `rejected_jobs` are added, all optional so one model
  parses both server generations. Documented as the accepted/rejected split, not as a rename.

## Non-Functional Requirements

- TDD, offline stubs; 100% line coverage retained on both annotations modules.
- mypy strict / black / bandit clean.
- **Backward compatible for existing callers:** no signature breaks; `push_graph` gains keyword-only
  arguments; the extra `list_workflows` call is skipped when `workflow_id` is supplied.

## Acceptance Criteria

- **Unit:** `bind_workflow` posts the exact envelope and parses `data`; `list_workflows` hits the
  workflows root with `o/p/s`; `push_graph` issues list → create → bind → upload → get **in order**,
  skips the list when `workflow_id` is given, prefers a published system workflow matching the batch
  type, raises `ValueError` with zero HTTP calls when nothing matches, and skips binding on a 404;
  `AnnotationBatch` parses a live new-server payload (no `completed_jobs`) **and** an old one.
- **Live (the actual bug):** against the local control plane at `eda4f9ca`, `push_graph` completes —
  batch ACTIVE, `total_jobs` equal to the rule-vertex count, `graph_uri` written — where rc14 raised
  a 500. Re-run `examples/quick_annotations.py` end to end.

## Out of Scope

- Changing comand. An early draft of this spec proposed having `create_batch` seed a `'*'` binding
  itself; that was wrong and is not being pursued. Binding is a **deliberate, separately-permissioned
  step**: `bind_workflow` is gated on `perm_annotation_workflows` EXECUTE rather than batch admin
  (noted in the handler at `annotations.rs:405`), because which workflow governs a batch is a policy
  choice the server cannot always infer — org 2 alone carries three workflows. Migration 94's `'*'`
  backfill is **compatibility for batches that predate the workflow model**, not a statement of the
  runtime contract. The current contract is create → bind → upload, and the defect is simply that
  `push_graph` never caught up with it. This track is therefore the complete fix, not a workaround.
- Workflow CRUD (`/api/annotation-workflows/_command`), version publishing, and reading existing
  bindings back off a batch — no consumer needs them yet.
- The orphan-DRAFT-batch-on-upload-failure follow-up (review C1 from PR #13), which remains queued.

## Branch

`fix/annotation-workflow-binding`, from `main` at `36a0c86`.
