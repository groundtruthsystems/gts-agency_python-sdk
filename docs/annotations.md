# Annotations — publishing a graph as work for annotators, and reading it back

Push an extracted knowledge graph to the control plane so humans can review it,
then recover what they did. The graph is the **same `create.graph` payload** an
agent already builds for the ontology sandbox (`run_id` / `vertices` / `edges`), so
an agent that produces one can publish it unchanged.

Entry point: `client.annotations()` → `AgencyAnnotationsClient`
(`/api/annotations`, plus `/api/annotation-specs` for the checklists and
`/api/annotation-workflows` for the review flows).

Publishing is [three calls](#there-is-no-single-publish-endpoint) wrapped by one;
[reading back](#reading-a-batch-back) is four.

## There is no single "publish" endpoint

Publishing is **three calls**, and knowing why matters when something fails:

1. **Create the batch** — `POST /api/annotations/_command` (`command: "create"`).
   The batch starts in **DRAFT** with `total_jobs = 0`. It is an empty container.
2. **Bind a workflow** — `POST /api/annotations/{batch_id}/_command`
   (`command: "bind_workflow"`). A batch is created governed by nothing, and the
   server refuses to insert a job into an unbound batch, so **without this the next
   step fails**. See [Binding a workflow](#binding-a-workflow) below.
3. **Upload the graph** — `POST /api/annotations/{batch_id}/upload`
   (`multipart/form-data`, one `file` field). This is what materialises the work:
   the server creates **one job per vertex whose `class` matches `target_class`**
   (default `rule`), attaches each vertex's `hops`-hop neighbourhood as context,
   stores the raw graph, and flips the batch to **ACTIVE** with `total_jobs` set —
   the `0/325` progress the annotation UI shows.

The upload's response body is **`null`**, so the job count only becomes visible on
a **read back** (`GET /api/annotations/{batch_id}`). `push_graph` does all four.

## The one-liner

```python
from agency_sdk.client import AgencyClient, CredentialsSupplier

client = AgencyClient(token_supplier=credentials, base_url="http://localhost:13001")

result = client.annotations().push_graph(
    organisation_id=2,
    name="MTUS Knee 2026",
    graph=sandbox_command["create"]["graph"],   # or file_path="…/graph.json"
    description="Rules extracted from the 2026 revision",
    instructions="Confirm each rule against its source page.",
)

print(result.batch_id, result.total_jobs, result.status)   # e.g. 7f1d…, 325, 1 (ACTIVE)
```

`graph` (a dict) and `file_path` are mutually exclusive — pass exactly one, or the
call raises `ValueError` **before** anything is created.

The individual legs are available when a caller wants them:

```python
annotations = client.annotations()

batch = annotations.create_batch(organisation_id=2, name="MTUS Knee 2026")   # DRAFT
annotations.bind_workflow(organisation_id=2, batch_id=batch.id, workflow_id="…")
annotations.upload_graph(organisation_id=2, batch_id=batch.id, graph=graph)  # -> ACTIVE
active = annotations.get_batch(organisation_id=2, batch_id=batch.id)
print(active.total_jobs, active.resolved_jobs)

page = annotations.list_batches(organisation_id=2, batch_type="graph")       # name → id
```

`BatchStatus` (`DRAFT=0, ACTIVE=1, COMPLETED=2, ARCHIVED=3`) names the integer
status the API returns.

**Job counters differ by server generation.** Current control planes report
`total_jobs` plus `resolved_jobs` / `accepted_jobs` / `rejected_jobs` — "done" and
"done well" being different questions. Older ones report a single `completed_jobs`.
All four are optional on the model so one SDK parses both; read `resolved_jobs` and
fall back to `completed_jobs` if you must support both.

## Binding a workflow

A workflow decides how a job is reviewed — who annotates, whether a second person
approves. Every job is governed by one, and the server resolves it **from the batch**
at insert time: it looks for a binding on `(batch_id, job_type)`, falls back to
`(batch_id, "*")`, and **refuses the insert** when neither exists.

A freshly created batch has no binding, so an unbound batch can hold no jobs. The
upload then fails with an opaque `500` whose real cause (`Batch has no workflow
binding for this job type and no default`) appears only in the control plane's log —
which is why this is worth knowing rather than discovering.

`push_graph` handles it. Doing it by hand:

```python
workflows = client.annotations().list_workflows(organisation_id=2)
graph_workflow = next(
    w for w in workflows.items
    if w.target_batch_type == "graph" and w.current_published_version_id
)
client.annotations().bind_workflow(
    organisation_id=2, batch_id=batch.id, workflow_id=graph_workflow.id
)
```

**Resolve, never hardcode.** The system workflows are seeded per organisation, so
`sys-wf-graph-2` is org 2's id and nobody else's. `push_graph` picks a workflow whose
`target_batch_type` matches the batch and that has a published version (the bind
resolves the *published* version server-side, so a draft-only workflow cannot be
bound), preferring system workflows. An organisation with several candidates is not
an error — pass `workflow_id=` to choose:

```python
client.annotations().push_graph(organisation_id=2, name="…", graph=graph,
                                workflow_id="org-wf-strict-review")
```

`job_type` defaults to `"*"`, the batch-wide default that covers every job type. Bind a
specific type only when one type needs a different flow from the rest of the batch.

Binding is a deliberate, separately-permissioned step: the server gates it on
workflow-execute rather than batch admin, because which workflow governs a batch is a
policy choice. Against a control plane predating workflows, the workflow lookup 404s
and `push_graph` skips the bind.

## Checklists: seed the job specification first

A job's checklist is seeded **at upload time** from the specification whose `code`
equals the upload's `job_type` (default `rule_validation`). If the org has no such
spec the upload still succeeds — and every job reaches its annotator with an empty
checklist. Seed it once per org, before the first push:

```python
from agency_sdk.delegates.annotations_dto import DEFAULT_JOB_TYPE   # "rule_validation"

annotations = client.annotations()
try:
    spec = annotations.get_spec(organisation_id=2, code=DEFAULT_JOB_TYPE)
except requests.HTTPError as error:
    if error.response is None or error.response.status_code != 404:
        raise
    annotations.create_spec(
        organisation_id=2,
        code=DEFAULT_JOB_TYPE,
        name="Rule validation",
        checklist=[
            {"id": "text_matches_source", "label": "Rule text matches the source"},
            {"id": "page_reference_correct", "label": "Page reference is correct"},
        ],
        instructions="Confirm each rule against its source document.",
    )
```

Get-then-create, not create-blindly: nothing server-side enforces that `code` is
unique per organisation, and the seeding lookup takes the first match. The SDK
deliberately offers no `ensure_spec` helper — that get-then-create is a two-call
race, and hiding it would only make the race invisible.

**Gotcha:** `get_spec` puts the **code** in the path
(`GET /api/annotation-specs/rule_validation`). The server's route names that
segment `{id}`, but it resolves it with a by-code lookup — a UUID there returns
404.

## Reading a batch back

Once annotators have worked a batch, four reads recover what they did. They are
read-only by design: a consumer of annotation output never writes to the control
plane, so the delegate exposes no transition, claim, or save methods.

```python
jobs = annotations.list_jobs(org, batch_id)                       # paged summaries
job = annotations.get_job(org, batch_id, jobs.items[0].id)        # the full row
ledger = annotations.list_job_transitions(org, batch_id, job.id)  # approval evidence
graph = annotations.get_graph(org, batch_id)                      # the upload, echoed back
```

### The list gives you summaries, not jobs

`list_jobs` returns identity and pipeline position — `id`, `state_code`,
`workflow_version_id`, the vertex labels, `audit_data` — and **none** of the
payloads. `vertex_data`, `connected_vertices`, `connected_edges`, `delta`, `data`,
`checklist_state` and `annotation_data` exist only on `get_job`.

That is the server's design, not an SDK shortcut: a list page never renders those
columns, and the query behind it uses a deferred join specifically to keep the wide
rows off the page. Plan for one `get_job` per job you actually need.

There is also **no server-side "completed" filter**, and the SDK does not fake one.
Filter `list_batches` on `status == BatchStatus.COMPLETED` yourself, then re-check
each job's `state_code` — batch completion is revertible, so the batch-level answer
can go stale under you.

### Query parameter names differ per endpoint

| Read | Params |
|---|---|
| `list_jobs` | `o` / `p` / `s` |
| `get_job` | `o` |
| `list_job_transitions` | **`organisation` / `page` / `size`** |
| `get_graph` | `o` |

The transitions route binds a different query type server-side (`OrganisationQuery`
rather than `JobListParams`), so the abbreviations do not work there. The SDK
mirrors each endpoint rather than normalising them — this inconsistency is the wire
contract, and "fixing" it would produce a request the server cannot bind.

### The edit columns are opaque

`annotation_data`, `checklist_state` and `delta` come back as whatever was stored,
unvalidated and unreshaped. The control plane never parses them — a save is a
whole-value column replacement — and their structure belongs to whichever front-end
wrote them. Two different apps write **different shapes into the same columns**, so
a consumer that cares must check which shape it got rather than assume.

`vertex_data` is the *original* vertex and is never overwritten by an annotator;
edits live in `annotation_data`. Nothing merges the two server-side, so a consumer
that wants the final content merges them itself.

### `revision` is a fence, not a version label

Approved content is not immutable server-side: a save arriving after approval still
lands. If you are assembling several jobs into one output, snapshot each job's
`revision` when you read it and re-read before you commit — a change means the job
moved underneath you and the assembled result is stale.

### The ledger carries PHI

> **Never log, trace, or export `JobTransitionEntry.note`.** It is clinician-written
> free text that may contain patient information, and the control plane deliberately
> keeps it out of its own access log. Re-exporting it from the SDK would defeat that.

The rest of the entry is the approval evidence: `transition_code`, `from_state` →
`to_state`, `actor_user_id`, `acting_as_role`, and `prior_actor_conflict` — which
marks a transition fired by someone who had already acted on that job. Treat the
conflict flag as a stop signal rather than a warning.

Note that an entry's `workflow_version_id` is the version **in force when it fired**,
not the job's current one. Bindings move; only the stamped copy makes a past
transition attributable to the policy that permitted it.

### The stored graph is returned unmodelled

`get_graph` hands back a plain `dict` — your own `{run_id, vertices, edges}` upload,
echoed back. It is deliberately not wrapped in a DTO: the SDK did not define that
payload and has no business constraining it. Use it as the pristine copy to
cross-check against per-job `vertex_data`.

One rough edge worth knowing: if the batch row points at an object that is not in
the store, this answers **500**, not 404 — the control plane does not translate the
object store's "no such key" into a not-found. So a batch whose graph was never
uploaded, or whose object was lost behind a surviving database row, looks the same
as a server fault. Treat a 500 here as "the graph is not retrievable" rather than
something to retry.

## Failure modes worth knowing

| Situation | What happens |
|---|---|
| Batch has no workflow bound | `500` with an opaque body — the job insert is refused. `push_graph` binds for you; a hand-rolled create → upload hits this. |
| Graph has no vertex of `target_class` | `400` — jobs would be empty, so the server refuses. The **batch stays DRAFT and empty**; it is not rolled back. |
| Batch already ACTIVE | `400` — upload requires DRAFT. Push a new batch instead. |
| Graph over 50 MiB | Rejected by the server's body limit. `requests` also assembles the whole multipart body in memory. |
| Neither / both of `graph` and `file_path` | `ValueError`, raised before any HTTP call. |
| Caller lacks annotations write | `403` (or `400 "User not supplied."` when the principal has no local user id — see below). |
| `get_graph` on a batch whose object is gone | `500`, not `404` — the object store's "no such key" is not translated. |

A push that dies on the upload leg leaves an **empty DRAFT batch** behind. It
holds no jobs, and `list_batches` finds it; the SDK does not archive it for you,
because deleting server state the caller did not ask about is not the SDK's call.

## Permissions

Every annotations handler requires a principal that has a **local user id** and
**organisation write on `Resource::Annotations`**. A machine-to-machine client
that authenticates but does not map to a local user is rejected with
`400 "User not supplied."` — that is a control-plane provisioning matter, not
something the SDK can work around.

## Scope

The delegate covers the publish path, its specifications, and the **read** side:
the batch read-back that proves the push landed, plus the four job-level reads
above. Deliberately **not** included: any *write* to a job (the `_command`
transitions, `/actions`, claims, checklist saves) — a consumer of annotation output
never writes back — along with dataset batches (`upload-dataset`), batch members,
the access audit log, and the `archive` / `unarchive` / `set_confidentiality`
commands. Add them when a consumer needs them.

## End-to-end example

[`examples/quick_annotations.py`](../examples/quick_annotations.py) runs the whole
flow against a live control plane — seed-or-find the spec, push from a dict and
from a file, read the batch and its jobs back (all four reads above), list, then
the 400 and `ValueError` paths — and archives every batch it created on the way
out.

```bash
python examples/quick_annotations.py
```
