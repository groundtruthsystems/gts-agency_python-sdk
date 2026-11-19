# Spec — paginated knowledge-graph list (SDK half)

Design: [design.md](./design.md) · Test plan: [test-plan.md](./test-plan.md) · Handover:
[solution-handover.md](../../solution-handover.md). Operator answers of 2026-11-18 are recorded in design.md.

## Overview

comand's `GET /api/knowledge-graphs?o=` is moving to the shared paged result
`{page:{page,size,total}, items}` with optional `p`/`s` (0-based, server defaults 0/10, no maximum,
ordered by `code`). The SDK's `AgencyGraphsClient.list`, `KnowledgeGraphList` and `FakeGraphs` must match
it, with no client-side defaults, limits or validation, and ship as the coordinated breaking release
`0.0.3rc1` after the gts-agency API is deployed.

## Functional Requirements

- **FR-01** `AgencyGraphsClient.list(organisation_id, *, page=None, size=None)`; `page`/`size` are keyword-only `int | None`.
- **FR-02** `list` always sends `o`; sends `p` only when `page is not None` and `s` only when `size is not None`.
- **FR-03** `list` applies no defaults, range checks or clamping; server HTTP errors surface as `requests.HTTPError` via `_make_request`.
- **FR-04** `KnowledgeGraphList` has a required `page: agency_sdk.domain.Page` plus `items`, keeping `extra="allow"`.
- **FR-05** `KnowledgeGraphEntry` is unchanged, including `repr=False` on `config` and `credential_variable_id`.
- **FR-06** `FakeGraphs.list` has the real signature, records `page`/`size` in `calls`, sorts by `code`, slices at `p*s`, defaults 0/10.
- **FR-07** `FakeGraphs`: past the end or `size=0` → empty `items` with real `total`; negative `p`/`s` → 500 `HTTPError` with a `SERVICE_ERROR` body.
- **FR-08** `FakeGraphs` builds `domain.Page` through an alias import; `FakeSessions` keeps `datasets_dto.Page`; `add_graph`/`error` unchanged.
- **FR-09** `docs/graphs.md` shows `page`/`size`, the `page` metadata, a walk-all example stopping on `page.total` or empty `items`, and bounded-size advice.
- **FR-10** `docs/graphs.md` states offset paging has no snapshot, so a catalogue change mid-walk can skip or repeat an item.
- **FR-11** `CLAUDE.md` no longer describes the graphs client as not paged.
- **FR-12** `CHANGE_LOG.md` and release notes mark the breaking change: API deploys first; older SDKs are unsupported against it.
- **FR-13** `pyproject.toml` version is `0.0.3rc1`.
- **FR-14** `test_contract_graphs.py` asserts the paged shape and `p`/`s` behaviour against a live comand.

## Non-Functional Requirements

- **NFR-01** No new pagination types or parameters; the `datasets_dto` duplicate `Page` is not used for graphs.
- **NFR-02** No other delegate, client, DTO, endpoint, `o` handling, `api_path` or base-client behaviour changes.
- **NFR-03** `mypy agency_sdk/` strict and `black` clean; coverage >80% on touched modules.
- **NFR-04** Tests, fixtures and docs use synthetic codes, org ids and canary tokens only; no PHI or secrets.
- **NFR-05** CI runs only the offline suite (unit + parity); the live contract smoke is a manual pre-publish check.

## Acceptance Criteria

Inbox note *Acceptance Criteria* items 3–7 are covered from the SDK side; items 1–2 are the gts-agency endpoint's.

- **AC-01** (FR-01, FR-02; note item 3) A call without `page`/`size` sends only `o` and parses the default `page` metadata — TS-01, TS-03, TS-27.
- **AC-02** (FR-01, FR-02, FR-04; note item 6) An explicit `page`/`size` is sent as `p`/`s` and returns `page` + `items` — TS-02, TS-04, TS-05, TS-15.
- **AC-03** (FR-03; note item 4) Out-of-range values pass through: past end and `size=0` give empty pages; negatives raise `HTTPError` — TS-07, TS-08, TS-09, TS-10, TS-28.
- **AC-04** (FR-04, FR-09; note item 5) Walking pages collects every `code` exactly once, including the last partial page and `size=0` — TS-06, TS-11, TS-12.
- **AC-05** (FR-04, FR-05) Bodies without a valid `page` fail validation; entry fields and `repr` redaction are unchanged — TS-13, TS-14, TS-16.
- **AC-06** (FR-06–FR-08; note item 7) `FakeGraphs` matches the mocked real client on every case, including an empty catalogue — TS-17, TS-18, TS-19, TS-20, TS-32, TS-34.
- **AC-07** (FR-09–FR-11) Docs and `CLAUDE.md` describe the paged contract; facade and exports keep working — TS-23, TS-24, TS-25.
- **AC-08** (NFR-02, NFR-04) Bearer stays out of errors; `o` always sent; base-client error paths unchanged; no `verify` override — TS-26, TS-29, TS-33.
- **AC-09** (NFR-03; note item 7) The offline suite, mypy and black are green with >80% coverage on touched modules — TS-31.
- **AC-10** (FR-12–FR-14, NFR-05) Before publishing `0.0.3rc1`, the live smoke passes against the deployed API and is recorded in the track — TS-21, TS-22, TS-30, TS-35.

## Out of Scope

- Any iterate-all-pages helper (possible follow-up only).
- Backward compatibility with the old `{items}` body or older SDKs.
- The comand endpoint, OpenAPI and comand-web (gts-agency worktree).
- Server-side size caps or turning the negative-value 500 into a 4xx.

## Open Questions

- Does the merged OpenAPI match handover §2 exactly? Checked by the first task; a mismatch is raised, not adapted.
