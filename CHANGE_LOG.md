# Change Log

> One paragraph per completed Sebenza track, newest first. A track's spec and plan are deleted at
> close-out; this file, the threat register in `.ai/sebenza/SECURITY.md`, and git history are the
> permanent record.

## Change Log

### 2026-08-22 — Workflow binding for push_graph + job-counter model fix (annotation_workflow_binding_20260822)

comand began requiring a workflow binding on an annotation batch before any job could be inserted, and nothing seeds one for a batch created through the API, so `push_graph` failed with an opaque 500 against every current control plane and left an empty DRAFT batch behind (issue #14). It now resolves a workflow, binds it, and only then uploads; `bind_workflow` and `list_workflows` became public because callers hitting this were hand-rolling the bind with raw `requests`. Workflow ids are resolved rather than hardcoded — the system workflows are seeded per organisation — and binding stayed an explicit step rather than something comand seeds automatically, because the server gates it on a different permission, which makes it a policy choice rather than a default the SDK may invent. The same release follows comand's split of `completed_jobs` into `resolved_jobs`/`accepted_jobs`/`rejected_jobs`, which had been turning a fully successful publish into a validation error on the read-back.

### 2026-08-03 — Annotations delegate: publish a rule graph as annotator work (annotations_client_20260803)

Added `AgencyClient.annotations()` so an agent can publish its extracted rule graph as work for human annotators, reusing the same `create.graph` payload it already sends to the ontology sandbox. Publishing is not one call: a batch is created in DRAFT, and the multipart graph upload is what materialises one job per vertex matching `target_class` and flips the batch to ACTIVE — and because that upload answers with a `null` body, the job count exists only after a read-back, which is why `push_graph` chains the legs and returns what the annotators will actually see. The delegate also covers the job specifications that seed each job's checklist, whose read resolves by `code` despite the route naming the segment `{id}`. Live-verified against a real control plane including the failure paths, and shipped as 0.0.1rc14.

### 2026-07-22 — Work-queue conflict envelope + queue-scoped owner lookup (conflict_envelope_20260718)

The platform moved work-queue 409 conflict bodies to the standard `{error:{…}}` envelope and made the external-ref lookup queue-scoped, so the SDK was parsing a shape that no longer existed — and `get_item_by_ref` was silently mapping the resulting router 404 to "no owner", which would have failed open in the consumer's exactly-once gate. Conflict parsing now reads the owner from `error.details` and keys an additive `contended` flag off `error.type == "CONFLICT_RETRY"`, a deterministic signal rather than an inference from missing details. `get_items_by_ref` replaced it, returning the list a queue-scoped lookup produces, since the same ref may legitimately be held once per queue. Two name-resolution list reads were added so a consumer can wire a queue or session template by name instead of a hardcoded id; shipped as 0.0.1rc13.

### 2026-07-18 — Work-queue ingestion delegate (files_inbox_ingestion_20260713)

Added `AgencyClient.work_queues()` over `/api/work_queues` — create-item-with-external-refs, publish, `add_ref`, owner lookup by ref, the unblock/retry/reprocess commands and item delete — mirroring the control plane's files-inbox ingestion contract. A 409 there is normal control flow rather than an error: an external ref can be claimed only once, so `create_item` and `add_ref` catch it and return a typed claim-lost result carrying the owning card's summary, which is what lets a consumer build an exactly-once ingestion gate on top. Verified end to end against the local stack with the control plane's own branch built in, and shipped as 0.0.1rc12.
