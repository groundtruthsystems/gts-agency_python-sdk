# Track annotation_workflow_binding_20260822 Context

- [Specification](./spec.md)
- [Implementation Plan](./plan.json)
- [Metadata](./metadata.json)

Upstream issue: [#14 — `push_graph` cannot create jobs on a batch it creates itself](https://github.com/groundtruthsystems/gts-agency_python-sdk/issues/14)

Both defects were re-confirmed against `gts-comand` `eda4f9ca` before any code was written: the
`annotation_job_before_insert` trigger and migration 94's one-time backfill in
`crates/comand/data/9{4,5}__annotation_state_*.sql`, `create_batch` never writing to
`annotation_batch_workflow`, and — live against the local control plane — a batch read returning
`total_jobs` / `resolved_jobs` / `accepted_jobs` / `rejected_jobs` and no `completed_jobs`.
