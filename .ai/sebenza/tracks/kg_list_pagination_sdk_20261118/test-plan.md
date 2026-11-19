# Test Plan — paginated knowledge-graph list (SDK)

Part of [design.md](./design.md).

## Strategy

SDK half only. Almost everything is proven offline with pytest and the stubbed `requests`
(`stub_requests`, `fake_credentials`). Integration means the fake-vs-mocked-real parity test and the
docs example run against `FakeGraphs`. The opt-in contract smoke (`pytest -m integration`,
`test_contract_graphs.py`) is the only real-comand check; it verifies `page.size` echo, `s=0` and the 500
on negatives, so it gates the release (UC-05), not the merge. Deliberately untested here: server
ordering/limits/authz (gts-agency), `datasets_dto.Page`, other delegates, performance at huge `size`.
Client-side validation does not exist, so tests assert its absence. No new tooling: pytest, pytest-cov,
mypy, black.

## Test Levels

| Level | Scope | Tooling | Where it runs |
|---|---|---|---|
| Unit | `list` wire params and parsing, DTO validation and repr, `FakeGraphs`, docs/CLAUDE.md assertions | pytest, `stub_requests`, `fake_credentials` | CI and local, offline |
| Integration (in-process) | Parity case table on `FakeGraphs` and the mocked real client; docs example on fakes; facade wiring | pytest parametrize, `adk_fakes` | CI and local, offline |
| Contract (opt-in) | Real comand returns the paged shape; `o`/`p`/`s` honoured; negatives → 500 | `pytest -m integration`, `docs/local_e2e.md` stack | Local stack, pre-release |
| Static | Keyword-only signature, types, formatting | mypy strict, black | CI and local |
| Manual release check | Breaking `CHANGE_LOG.md` entry, version bump, API deployed first | Workflow phase verification | Release maintainer |

## Scenarios

| TS | Covers | Level | Pri | Given/When/Then | Evidence |
|---|---|---|---|---|---|
| TS-01 | UC-01, AC-3 | Unit | P1 | Paged stub; `list(org)` with no page/size; params exactly `{o}`; `page` parsed | params == `{o}`; `page` fields match body |
| TS-02 | UC-02, AC-6 | Unit | P1 | `list(org, page=1, size=5)`; params `o,p=1,s=5`; metadata parsed | params == `{o,p:1,s:5}`; `page.page==1`, `page.size==5` |
| TS-03 | UC-02 | Unit | P2 | Only `page=2`, then only `size=3`; the omitted param is absent | no `s` (resp. `p`) sent; no client default |
| TS-04 | UC-02, UC-02b | Unit | P2 | `page=0`, `size=0` are sent (`is not None`, not truthiness) | params contain `p=0`, `s=0` |
| TS-05 | UC-02, AC-6 | Static/Unit | P2 | `page`/`size` keyword-only, default None; `list(org, 1)` fails | `TypeError`; signature kinds KEYWORD_ONLY; mypy clean |
| TS-06 | UC-03a | Unit | P1 | Total 12, size 5, page 2 → partial page | `len(items)==2`, `page.total==12` |
| TS-07 | UC-02a | Unit | P1 | Page past the end → 200 empty items, real total | `items == []`, real `page.total`, no exception |
| TS-08 | UC-02b | Unit | P1 | `size=0` → 200 empty items | `items == []`, `page.size == 0`, `s=0` sent |
| TS-09 | UC-02c, T-3 | Unit | P1 | `page=-1`; stub returns 500 SERVICE_ERROR; request still sent | `HTTPError`; one call recorded with `p=-1` |
| TS-10 | UC-02c, T-2, T-3 | Unit | P2 | `size=-1` and `size=10**9` pass through unchanged | params carry `s=-1` / `s=10**9`; negative → HTTPError; no clamp |
| TS-11 | UC-03, UC-03a, T-7 | Unit | P1 | 25 synthetic codes; walk pages until `p*s >= page.total` | all 25 codes collected exactly once |
| TS-12 | UC-03, data risk | Unit | P2 | Documented walk with `size=0` stops when `items == []` | exactly one request made; returns no codes |
| TS-13 | UC-01, T-7 | Unit | P1 | Old `{items}` body (no `page`) via client and DTO | `pydantic.ValidationError` |
| TS-14 | UC-01, T-7 | Unit | P2 | Malformed `page` (string total, missing `size`, null) | `ValidationError` each; no tolerant parse |
| TS-15 | UC-01, AC-6 | Unit | P1 | DTO parses paged body; `page` is `agency_sdk.domain.Page`; extra keys allowed | `type(page) is domain.Page`; extra kept |
| TS-16 | AC-2 (SDK), T-6 | Unit | P1 | Entry with canary `config`/`credential_variable_id`; repr entry and list | canaries absent from `repr`/`str`; `enabled` parsed |
| TS-17 | UC-04 | Integration | P1 | `inspect.signature(FakeGraphs.list)` equals the real one | signatures equal |
| TS-18 | UC-04, AC-7 | Integration | P1 | Shared case table (defaults, p=1/s=5, last, past end, s=0, p=-1, s=-1) on fake and mocked client | identical `items`, `page`, error type/status |
| TS-19 | UC-04 | Unit | P2 | Fake with 11 unsorted graphs; default list | sorted by `code`; 10 items, `size==10`, `total==11`; `calls` records page/size; `type(page) is agency_sdk.domain.Page` |
| TS-20 | UC-04, UC-02c | Unit | P2 | Fake with negative page/size; `error` injection; `add_graph` | 500 `HTTPError`; injected error propagates |
| TS-21 | App risk (fake drift) | Contract | P1 | Opt-in: TS-18 case table against local comand vs fake | `page.size` echo, `s=0` → 200, negatives → 500 match |
| TS-22 | UC-01, UC-02, AC-2 | Contract | P2 | Opt-in: `list(org)` on real server | `page` present; codes ascending; `enabled` present |
| TS-23 | UC-05, data risk | Integration | P2 | `docs/graphs.md` shows page/size, metadata, walk example, no-snapshot caveat, bounded-size advice; example runs on fakes | docs test asserts each string incl. bounded-size advice; example collects all codes once |
| TS-24 | UC-05 | Unit | P3 | `CLAUDE.md` no longer calls the graphs client "not paged" | string absent; delegate line present |
| TS-25 | UC-04, facade | Unit | P2 | `AgencyClient.graphs()` wiring and root exports | `test_client_facade.py` passes |
| TS-26 | T-4, T-8 | Unit | P1 | Canary-token tests rerun with paging args and a 500 body | canary absent from exception text/repr/URL/params |
| TS-27 | T-1, T-5 | Unit | P2 | `o` sent for every page/size combination | every request has `o == org` |
| TS-28 | T-2 | Unit | P3 | Default call sends no `s`; no client max | TS-01 + TS-10 evidence; docs advise bounded sizes |
| TS-29 | Base-client risk | Unit | P2 | Existing transport and 401/403/500 tests rerun with paging args | same exception types and call counts |
| TS-30 | UC-05, version skew | Manual | P1 | Breaking `CHANGE_LOG.md` entry with ordering; version bumped; API deployed | checklist; diffs; TS-21/22 green |
| TS-32 | UC-02a, AC-7 empty | Unit+Integration | P1 | Empty catalogue (`total=0`) via client, walk and parity table | `items == []`, `page.total == 0`; walk makes one request |
| TS-34 | UC-04, data risk (Page alias) | Unit | P1 | Fake result round-trips; `FakeSessions` search still builds `datasets_dto.Page` | `KnowledgeGraphList.model_validate(r.model_dump())` ok; existing `FakeSessions` tests green |
| TS-35 | Data risk (fake drift) | Contract | P3 | Opt-in: codes with mixed case/hyphens compared fake vs server order | same order, else recorded as accepted drift |
| TS-33 | T-8 | Unit | P3 | `list()` passes no `verify` override to `requests` | captured kwargs lack `verify=False` |
| TS-31 | AC-7 coverage bar | Unit | P1 | Full suite with coverage on touched modules | >80% on touched modules; mypy/black clean |

## Traceability

| UC | Scenarios |
|---|---|
| UC-01 | TS-01, TS-13, TS-14, TS-15, TS-22, TS-25 |
| UC-02 | TS-02, TS-03, TS-04, TS-05, TS-22, TS-27 |
| UC-02a | TS-07, TS-32 |
| UC-02b | TS-04, TS-08 |
| UC-02c | TS-09, TS-10, TS-20 |
| UC-03 | TS-11, TS-12 |
| UC-03a | TS-06, TS-11 |
| UC-04 | TS-17, TS-18, TS-19, TS-20, TS-25, TS-34 |
| UC-05 | TS-23, TS-24, TS-30 |

| AC / handover §3 | Scenarios |
|---|---|
| AC-2 (SDK view) | TS-15, TS-16, TS-22 |
| AC-3 | TS-01 |
| AC-4 | TS-07, TS-08, TS-09, TS-10 |
| AC-5 (SDK walk) | TS-06, TS-11, TS-12 |
| AC-6 | TS-02, TS-05, TS-15 |
| AC-7 (SDK part) | TS-01, TS-02, TS-06–TS-10, TS-18, TS-31, TS-32 |
| §3 client tests | TS-01, TS-02, TS-06, TS-07, TS-08, TS-09, TS-11 |
| §3 DTO tests | TS-13, TS-15, TS-16 |
| §3 parity | TS-17, TS-18 |
| §3 docs/facade keep passing | TS-23, TS-24, TS-25 |

| Risk / threat | Scenarios |
|---|---|
| T-1 | TS-27 |
| T-2 | TS-10, TS-23, TS-28 |
| T-3 | TS-09, TS-10, TS-26, TS-29 |
| T-4 | TS-26 |
| T-8 | TS-26, TS-33 |
| T-5 | TS-22, TS-27 |
| T-6 | TS-16 |
| T-7 | TS-11, TS-13, TS-14 |
| Fake/server drift; contract as written | TS-18, TS-21, TS-22, TS-35 |
| `p*s` overflow 500 (not modelled in fake) | accepted gap — server-owned (gts-agency) |
| Fake `Page` alias / `FakeSessions` regression | TS-19, TS-34 |
| Version coupling / rollback | TS-13, TS-30 |
| Walk on `len(items)<size`, `size=0` loop | TS-11, TS-12, TS-23 |
| Offset paging without snapshot | TS-23 |
| Base-client error path regressions | TS-29 |

## Test Data & Environments

- Synthetic catalogue only: codes `kg-001`…`kg-025`, names "Sample graph N"; no real graph codes.
- Org id: arbitrary integer (e.g. 1); the contract smoke reads its org from the existing env var.
- Canary strings (e.g. `CANARY-NOT-A-SECRET`) for `config`, `credential_variable_id` and the bearer.
- Stubbed paged bodies built by a helper that slices at `p*s`; the parity case table is defined once.
- `add_graph` called in unsorted order so the sort-by-`code` check is meaningful.
- Offline CI: `requests` stubbed in `conftest.py`; contract smoke only on the `docs/local_e2e.md` stack, never production.
- Python 3.12, `pip install -e ".[dev]"`; no extras needed.

## Entry & Exit Criteria

- Entry: gts-agency endpoint + OpenAPI merged and checked against handover §2; a mismatch is raised, not adapted.
- Entry: TS-01–TS-20 and TS-32 written and confirmed red before implementation (TDD).
- Exit: every UC and AC above has at least one passing scenario; every §3 required test exists.
- Exit: offline `pytest` green, including `test_graphs_docs.py` and `test_client_facade.py`.
- Exit: coverage >80% on touched modules; `mypy agency_sdk/` and `black --check` clean.
- Exit: no real credential, org data or PHI in fixtures; TS-16 and TS-26 pass.
- Release gate: paginated API deployed and TS-21/TS-22 pass against it.
- Release gate: breaking `CHANGE_LOG.md` entry and version bump present (TS-30).

## Coverage Map

Not produced: the traceability tables carry the fan-out, and TS-17–TS-21 share one case table.

## Open Questions

1. Is the opt-in contract smoke (TS-21/22) accepted as the sole real-server check and a hard release gate?
2. Does the fake's 500 carry the `SERVICE_ERROR` envelope body or status only? (pinned by TS-18/TS-20)
3. Version for the breaking bump changes TS-30's expected evidence.
