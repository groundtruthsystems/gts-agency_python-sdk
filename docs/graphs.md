# Knowledge graphs — listing an org's knowledge graphs

One read-only call through the control plane (comand).

Entry point: `client.graphs()` → `AgencyGraphsClient` (`/api/knowledge-graphs`).

| Call | Request | Returns |
|---|---|---|
| `list(org_id)` | `GET /api/knowledge-graphs?o=` | `KnowledgeGraphList(items=[KnowledgeGraphEntry])` |

The DTOs are importable from the package root: `from agency_sdk import KnowledgeGraphList, KnowledgeGraphEntry`.

## Example

<!-- graphs-example -->
```python
catalogue = client.graphs().list(org_id)
enabled = [g.code for g in catalogue.items if g.enabled]
```

## `list`

Returns comand's built-in catalogue (today only `medical-ontology`) with the org's `enabled` flag. Not
paged. `credential_variable_id` and `config` are present only when comand sends them, and both are
hidden from `repr`.

Errors propagate unchanged: an HTTP status raises `requests.HTTPError`, a transport failure the plain
`requests` exception, and a malformed body `pydantic.ValidationError`. The call keeps the base client's
connection retry (3 attempts, 30 s timeout); read timeouts and HTTP statuses are never retried.

## Security

- The bearer token travels only in the `Authorization` header. It is not in any exception message or DTO,
  but `exc.response.request.headers` **does** hold it: never log `exc.response.request.headers` or the
  prepared request.
- Never log `config` or `credential_variable_id` from a `KnowledgeGraphEntry`; they are operator
  configuration and are hidden from `repr` for that reason.
- Never send the client's token to `config.base_url`. That URL is a service behind comand, which injects
  its own key when it proxies to it.
- Do not build the client with an `http://` base URL outside local development: the token would travel in
  clear text.

## Testing offline

`agency_sdk.test.adk_fakes.FakeGraphs` (also `FakeControlPlane().graphs`) has the same signature:

```python
from agency_sdk.test.adk_fakes import FakeGraphs, http_error

graphs = FakeGraphs()
graphs.add_graph("medical-ontology", name="Medical Ontology")
graphs.error = http_error(500)  # every call raises it until reset to None
```

## Contract smoke

`pytest -m integration agency_sdk/test/test_contract_graphs.py` runs against a non-production comand
with a dedicated service account (see the module docstring). A skip never counts as a pass for release.
