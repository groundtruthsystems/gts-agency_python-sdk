# Knowledge graphs — listing graphs and reading a condition's subgraph

Two read-only calls through the control plane (comand). The research agent uses them to decide
whether a condition already exists in the knowledge graph (*enrich*) or must be created (*create*).

Entry point: `client.graphs()` → `AgencyGraphsClient` (`/api/knowledge-graphs`).

| Call | Request | Returns |
|---|---|---|
| `list(org_id)` | `GET /api/knowledge-graphs?o=` | `KnowledgeGraphList(items=[KnowledgeGraphEntry])` |
| `condition_subgraph(org_id, code, *, depth=1, direction="any")` | two `GET`s through comand's medical-ontology proxy (below) | `ConditionSubgraph(root, vertices, edges)` |

The DTOs and both exceptions are importable from the package root:
`from agency_sdk import ConditionNotFound, ConditionClassMismatch, ConditionSubgraph, KnowledgeGraphList, KnowledgeGraphEntry`.

## Example

<!-- graphs-example -->
```python
from agency_sdk import ConditionClassMismatch, ConditionNotFound

catalogue = client.graphs().list(org_id)
assert any(g.code == "medical-ontology" and g.enabled for g in catalogue.items)

try:
    subgraph = client.graphs().condition_subgraph(org_id, code)
    mode = "enrich"  # subgraph.root, subgraph.vertices, subgraph.edges
except ConditionNotFound:
    mode = "create"
except ConditionClassMismatch:
    raise  # the code names something that is not a condition: stop, do not create
```

Any other exception (`requests.HTTPError`, `ConnectionError`, `ReadTimeout`, `pydantic.ValidationError`)
means *unknown*, never *not found*. Retry or stop; do not create.

## `list`

Returns comand's built-in catalogue (today only `medical-ontology`) with the org's `enabled` flag. Not
paged. `credential_variable_id` and `config` are present only when comand sends them, and both are
hidden from `repr`.

## `condition_subgraph`

`code` is `C` + 7 digits (UMLS CUI) or `B` + 7 digits (authored id), checked with `re.fullmatch`. A bad
code, a `depth` outside 1–6 (or not an `int`) or a `direction` other than `out`/`in`/`any` raises
`ValueError` before any request; the message never repeats the input.

1. **Resolve** — `GET /api/knowledge-graphs/medical-ontology/api/models/_/{code}?o=&source=code`. Both code
   families take this path. The response's `bid` must be a UUID and its `class` must be `condition`.
2. **Walk** — `GET /api/knowledge-graphs/medical-ontology/api/models/condition/{bid}/_graph?o=&depth=&direction=`.
   The default `direction="any"` includes inbound edges such as Medication -treats-> Condition.

`root` is the resolved entity document; `vertices` and `edges` are the walk's raw dicts (the ontology
documents are open and unmodelled). An empty walk is still a found condition.

### The not-found contract

Only a **resolve 404 whose body is exactly `{"detail": "not found"}`** (the ontology service's own body)
raises `ConditionNotFound`, an `HTTPError` that keeps `response`. Everything else fails closed:

- a 404 with any other body (comand or a gateway misrouting the request) → plain `HTTPError`;
- any other status, any walk failure, any transport error → the plain `requests` exception;
- a resolved entity whose `class` is not `condition` → `ConditionClassMismatch` (a `LookupError`, not an
  `HTTPError`): stop, do not create;
- a malformed body → `pydantic.ValidationError`.

Resolve comes first because `_graph` answers an unknown `bid` with an empty 200.

### Known limits

- **Secondary codes.** The ontology service matches only an entity's first code. A condition stored under
  a secondary code reads as not found. Confirming before creating is the agent's job.
- **Authored `B…` ids** are found only if the ontology service stores them in an entity's `code`. The
  contract smoke test (`test_contract_graphs.py::test_authored_b_code`) checks this.
- **Retries.** Both calls keep the base client's connection retry (3 attempts, 30 s timeout). Read
  timeouts and HTTP statuses are never retried; anything more is the caller's job.

## Security

- The bearer token travels only in the `Authorization` header. It is not in any exception message or DTO,
  but `exc.response.request.headers` **does** hold it: never log `exc.response.request.headers` or the
  prepared request.
- Never log `config` or `credential_variable_id` from a `KnowledgeGraphEntry`; they are operator
  configuration and are hidden from `repr` for that reason.
- Never send the client's token to `config.base_url`. That URL is the ontology service behind comand; the
  SDK reaches it only through comand's proxy, which injects its own key.
- Do not build the client with an `http://` base URL outside local development: the token would travel in
  clear text.
- comand's proxy does not validate path segments; the SDK only ever sends validated codes and UUID bids.

## Testing offline

`agency_sdk.test.adk_fakes.FakeGraphs` (also `FakeControlPlane().graphs`) has the same signatures:

```python
from agency_sdk.test.adk_fakes import FakeGraphs, http_error

graphs = FakeGraphs()
graphs.add_graph("medical-ontology", name="Medical Ontology")
graphs.add_condition("C0000001", {"bid": "<uuid>", "class": "condition"}, vertices=[], edges=[])
graphs.error = http_error(500)  # every call raises it until reset to None
```

## Contract smoke

`pytest -m integration agency_sdk/test/test_contract_graphs.py` runs against a non-production comand. It
needs a dedicated service account and two operator-supplied codes (see the module docstring). A missing
code fails the run; a skip never counts as a pass for release.
