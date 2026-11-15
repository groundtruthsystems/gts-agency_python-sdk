# Spec — Sangoma SDK knowledge-graph list & condition subgraph

Track `sangoma_sdk_graph_discovery_20261114`. Refines the operator-approved [design](./design.md) (approved
2026-11-14) and its [test plan](./test-plan.md). Governing instruction: the solution handover as amended by
the operator on 2026-11-14 (G1 `_find` and G2 create-only upload dropped).

## Overview

gts-sangoma-agent is given a condition code (`C…` UMLS CUI or `B…` authored id). Before researching it must
read the condition's known subgraph, or learn that the knowledge graph has none (create mode). The SDK adds
a new `graphs()` delegate with two typed reads through comand, the org's knowledge-graph catalogue and a
condition's subgraph, plus offline fakes. No server, upload or agent change.

## Functional Requirements

- **FR-01** `AgencyClient.graphs()` returns an `AgencyGraphsClient` (subclass of `BaseDelegateClient`, `api_path="/api/knowledge-graphs"`) sharing the client's `CredentialsSupplier`.
- **FR-02** `graphs().list(org)` sends `GET /api/knowledge-graphs` with params exactly `{"o": org}` and returns `KnowledgeGraphList`.
- **FR-03** `KnowledgeGraphList.items` is required; `KnowledgeGraphEntry` requires `code`, `name`, `description`, `enabled`; `credential_variable_id` and `config` are optional.
- **FR-04** `graphs().condition_subgraph(org, code, *, depth=1, direction="any")` returns `ConditionSubgraph(root, vertices, edges)`.
- **FR-05** It resolves via `GET …/medical-ontology/api/models/_/{code}` with params `{"o": org, "source": "code"}`, for both `C` and `B` codes.
- **FR-06** It walks via `GET …/medical-ontology/api/models/condition/{bid}/_graph` with params `{"o", "depth", "direction"}`; `root` comes from the resolve response.
- **FR-07** A resolve 404 whose JSON body is exactly `{"detail": "not found"}` raises `ConditionNotFound` (an `HTTPError` keeping `response`), and no walk is sent.
- **FR-08** Any other status, any other 404 body, any walk error and any transport error propagates as a plain `requests` exception, never `ConditionNotFound`.
- **FR-09** A code not fully matching `[CB][0-9]{7}`, a non-int or out-of-range (1–6) `depth`, or a `direction` outside `out`/`in`/`any` raises `ValueError` before any request.
- **FR-10** The `ValueError` message states the expected format and never echoes the input.
- **FR-11** A resolved root whose `class` is not `condition` raises `ConditionClassMismatch` (a `LookupError`), and no walk is sent.
- **FR-12** A root missing `class`, or whose `bid` is not a UUID, and a walk body without `vertices`/`edges` lists, raise a validation error; nothing is returned.
- **FR-13** A walk 200 with empty `vertices`/`edges` is a found condition, not *not found*.
- **FR-14** The new DTOs and both exceptions are re-exported from `agency_sdk/__init__.py`.
- **FR-15** `adk_fakes.py` gains `FakeGraphs` (on `FakeControlPlane` as `graphs`) with signatures identical to the real methods, `add_graph`, `add_condition(code, root, vertices, edges)` seeding, the same not-found/mismatch/`ValueError` behaviour, and `http_error` injection.
- **FR-16** Docs: a graphs doc (`docs/graphs.md`), the `CLAUDE.md` delegate list (eleven → twelve), and a `CHANGE_LOG.md` entry at release.

## Non-Functional Requirements

- **NFR-01** The bearer token never appears in any exception `str`/`repr`/`args`, DTO `repr`/dump, or log; only in the `Authorization` header.
- **NFR-02** `KnowledgeGraphEntry.config` and `credential_variable_id` are hidden from `repr` (`repr=False`).
- **NFR-03** Reads use the base client's default GET connection retry (3 attempts) and 30 s timeout; `ReadTimeout` and HTTP statuses are not retried.
- **NFR-04** `mypy --strict` and `black` clean; >80% coverage on `graphs_client.py`, `graphs_dto.py` and `FakeGraphs`; the existing suite passes unmodified.
- **NFR-05** Docs warn against logging `exc.response.request.headers` or `config`, against `http://` base URLs, and against ever sending the token to `config.base_url`.

## Acceptance Criteria

- **AC-01** (FR-02, FR-03) List returns typed entries and sends only `o`; errors and malformed bodies raise — TS-01, TS-02, TS-03, TS-04.
- **AC-02** (FR-04–FR-06) A known `C` or `B` code yields the subgraph with the exact wire requests and defaults — TS-05, TS-06, TS-07, TS-08.
- **AC-03** (FR-07, FR-08) *Not found* is raised only on the MOS not-found body; every other failure is a plain error — TS-09, TS-10, TS-11, TS-12, TS-32.
- **AC-04** (FR-09, FR-10, FR-12) Unsafe codes, server bids and malformed bodies are refused before use — TS-13, TS-14, TS-16.
- **AC-05** (FR-11, FR-13) Class mismatch stops; an empty graph is found — TS-15, TS-17.
- **AC-06** (NFR-01, NFR-02) Token and `config` secrecy — TS-18, TS-19, TS-20.
- **AC-07** (FR-01, FR-14) Accessor and root exports — TS-21.
- **AC-08** (FR-15) Fakes match the real contract — TS-22, TS-23, TS-24.
- **AC-09** (NFR-04, NFR-05, FR-16) Quality gates and docs — TS-29, TS-30.
- **AC-10** (UC-04) Contract smoke against non-prod comand **passes (not skips)** before release — TS-25, TS-26, TS-27, TS-28, TS-31.

## Out of Scope

- G1 `_find` discovery and `GraphLookup`; G2 `files().upload(create_only=…)` and `FileExistsConflict`.
- Calling the ontology service directly, or any comand/MOS change (TM-9 is raised with gts-agency).
- Retry/backoff beyond the base client, research or create-mode logic (agent-owned).
- Typed modelling of ontology vertices/edges (raw dicts).
- Changes to `CredentialsSupplier`, the base client or other delegates.

## Open Questions

1. Which known `C…` and authored `B…` conditions seed TS-25/TS-31 (env-supplied by the operator)?
2. Are authored `B…` ids reachable via `source=code`? TS-31 decides; a MOS 404 there means amend the design before release.
3. Secondary-code false negatives: the agent owns any confirmation before creating; the SDK documents the limit.
4. Can `config` carry secrets, and should comand hide it from read-only callers? (comand-owned; SDK hides it from `repr` regardless.)
