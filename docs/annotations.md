# Annotations — publishing a graph as work for annotators

Push an extracted knowledge graph to the control plane so humans can review it.
The graph is the **same `create.graph` payload** an agent already builds for the
ontology sandbox (`run_id` / `vertices` / `edges`), so an agent that produces one
can publish it unchanged.

Entry point: `client.annotations()` → `AgencyAnnotationsClient`
(`/api/annotations`, plus `/api/annotation-specs` for the checklists).

## There is no single "publish" endpoint

Publishing is **two calls**, and knowing why matters when something fails:

1. **Create the batch** — `POST /api/annotations/_command` (`command: "create"`).
   The batch starts in **DRAFT** with `total_jobs = 0`. It is an empty container.
2. **Upload the graph** — `POST /api/annotations/{batch_id}/upload`
   (`multipart/form-data`, one `file` field). This is what materialises the work:
   the server creates **one job per vertex whose `class` matches `target_class`**
   (default `rule`), attaches each vertex's `hops`-hop neighbourhood as context,
   stores the raw graph, and flips the batch to **ACTIVE** with `total_jobs` set —
   the `0/325` progress the annotation UI shows.

The upload's response body is **`null`**, so the job count only becomes visible on
a **read back** (`GET /api/annotations/{batch_id}`). `push_graph` does all three.

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
annotations.upload_graph(organisation_id=2, batch_id=batch.id, graph=graph)  # -> ACTIVE
active = annotations.get_batch(organisation_id=2, batch_id=batch.id)
print(active.total_jobs, active.completed_jobs)

page = annotations.list_batches(organisation_id=2, batch_type="graph")       # name → id
```

`BatchStatus` (`DRAFT=0, ACTIVE=1, COMPLETED=2, ARCHIVED=3`) names the integer
status the API returns.

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

## Failure modes worth knowing

| Situation | What happens |
|---|---|
| Graph has no vertex of `target_class` | `400` — jobs would be empty, so the server refuses. The **batch stays DRAFT and empty**; it is not rolled back. |
| Batch already ACTIVE | `400` — upload requires DRAFT. Push a new batch instead. |
| Graph over 50 MiB | Rejected by the server's body limit. `requests` also assembles the whole multipart body in memory. |
| Neither / both of `graph` and `file_path` | `ValueError`, raised before any HTTP call. |
| Caller lacks annotations write | `403` (or `400 "User not supplied."` when the principal has no local user id — see below). |

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

The delegate covers the publish path, its specifications, and the read-back that
proves the push landed. Deliberately **not** included: dataset batches
(`upload-dataset`), job reads/updates (`/jobs`), the stored-graph read
(`/{batch_id}/graph`), batch members, the access audit log, and the
`archive` / `unarchive` / `set_confidentiality` commands. Add them when a consumer
needs them.

## End-to-end example

[`examples/quick_annotations.py`](../examples/quick_annotations.py) runs the whole
flow against a live control plane — seed-or-find the spec, push from a dict and
from a file, read back, list, then the 400 and `ValueError` paths — and archives
every batch it created on the way out.

```bash
python examples/quick_annotations.py
```
