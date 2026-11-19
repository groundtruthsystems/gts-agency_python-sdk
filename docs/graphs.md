# Knowledge graphs — listing an org's knowledge graphs

One read-only call through the control plane (comand).

Entry point: `client.graphs()` → `AgencyGraphsClient` (`/api/knowledge-graphs`).

| Call | Request | Returns |
|---|---|---|
| `list(org_id, *, page=None, size=None)` | `GET /api/knowledge-graphs?o=[&p=][&s=]` | `KnowledgeGraphList(page=Page, items=[KnowledgeGraphEntry])` |

The DTOs are importable from the package root: `from agency_sdk import KnowledgeGraphList, KnowledgeGraphEntry`.
`page` is the shared `agency_sdk.domain.Page` (`page`, `size`, `total`).

## Example

<!-- graphs-example -->
```python
size = 25  # keep size bounded: the server has no maximum
codes, enabled, page = [], [], 0
while True:
    result = client.graphs().list(org_id, page=page, size=size)
    codes += [g.code for g in result.items]
    enabled += [g.code for g in result.items if g.enabled]
    # Stop on the last page by total, or on an empty page (size=0 or a page past the end never fills).
    if not result.items or (page + 1) * size >= result.page.total:
        break
    page += 1
```

One page is a single call: `client.graphs().list(org_id)` returns the first 10 entries, and
`client.graphs().list(org_id, page=1, size=5)` the sixth to tenth.

## `list`

Returns one page of comand's built-in catalogue (today only `medical-ontology`) with the org's `enabled`
flag, sorted by `code`. `credential_variable_id` and `config` are present only when comand sends them, and
both are hidden from `repr`.

### Paging

The response follows the repository's standard paged result: `{"page": {"page", "size", "total"}, "items"}`.

- Pages are 0-based; the offset is `page * size`.
- `page` and `size` are keyword-only and are sent as `p` / `s` only when you pass them. Omitted, the server
  defaults apply: `page=0`, `size=10`. The SDK applies no defaults, limits or range checks of its own.
- `page.page` and `page.size` echo the request (or the defaults); `page.total` counts every entry across all
  pages. Walk until `(page + 1) * size >= page.total`, and also stop on an empty page, as the example does:
  never stop on `len(items) < size`, which never ends at `size=0`.
- A page past the end, or `size=0`, is an empty page that still carries the real `total`.
- A negative `page` or `size` (or one whose product overflows) is answered with a 500 `SERVICE_ERROR`, raised
  as `requests.HTTPError`. The request is still sent: the SDK does not validate.
- There is no maximum `size` on the server or in the SDK, so pass a bounded `size` (tens, not millions).
- Offset paging has no snapshot: if the catalogue changes during a walk, an entry can be skipped or
  repeated. The catalogue is small and operator-managed, so this is rare; de-duplicate by `code` if it matters.

Breaking change in `0.0.3rc1`: the response gained the required `page`. This SDK cannot parse the old unpaged
body, and older SDKs cannot parse the new one, so upgrade only once your comand serves the paged endpoint.

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

`agency_sdk.test.adk_fakes.FakeGraphs` (also `FakeControlPlane().graphs`) has the same signature and pages
exactly as comand does (sorted by `code`, the same defaults, empty pages and 500 on negative values):

```python
from agency_sdk.test.adk_fakes import FakeGraphs, http_error

graphs = FakeGraphs()
graphs.add_graph("medical-ontology", name="Medical Ontology")
graphs.list(2, page=0, size=5).page.total  # 1
graphs.error = http_error(500)  # every call raises it until reset to None
```

## Contract smoke

`pytest -m integration agency_sdk/test/test_contract_graphs.py` runs against a non-production comand
with a dedicated service account (see the module docstring). A skip never counts as a pass for release.
