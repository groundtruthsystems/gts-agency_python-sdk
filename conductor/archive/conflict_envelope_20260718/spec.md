# Spec: Conflict-body envelope parsing (work-queue 409) → rc13

## Overview

gts-agency Track ① is moving the work-queue 409 conflict bodies from a **flat top-level
domain object** (`{work_item_id, status, published}`) to the platform's **standard error
envelope** (`{error: {message, type, details}}`), and adds a distinct **`error.type =
"CONFLICT_RETRY"`** for the owner-less fallback (the `AppError::Conflict(String)` case, e.g.
"claim contended"). This SDK track updates the conflict parsing in `AgencyWorkQueueClient`
(`_conflict_body` + `create_item`/`add_ref`) to read the owner from `error.details` and to
key a new `contended` flag off `error.type == "CONFLICT_RETRY"` — a **deterministic source**,
not an inference from missing details.

Two 409 kinds:

- **owner-known claim conflict** — `error.details` present → the owning card's summary.
- **owner-less / transient "contended"** — `error.type == "CONFLICT_RETRY"` → no owner.

The change is **wire-internal**: ③'s public API is preserved (create_item still returns
`existing={work_item_id, status, published}`, add_ref still returns the owner), with one
**additive** `contended` flag for the owner-less case. Ends by publishing **rc13**.

## Cross-repo dependency (repo boundaries)

Adding the `CONFLICT_RETRY` type and the envelope reshape is **① server work — OUT OF SCOPE
here** (SDK tracks contain only SDK work). This track **mirrors/consumes** ①'s reshaped
contract, exactly as the rc12 work-queue delegate mirrored ①'s frozen contract. The live
Gate-A verification depends on ①'s branch carrying **both** the envelope and `CONFLICT_RETRY`.

## Functional Requirements

- **FR1 — envelope + type extraction:** conflict parsing reads `error = body.get("error", {})`,
  then `details = error.get("details")` (owner) and `type = error.get("type")`. A 409 whose
  body will not parse as JSON, or is not the `{error:{…}}` envelope, and is neither an
  owner-details conflict nor `CONFLICT_RETRY`, returns no usable conflict → the original
  `HTTPError` re-raises (keep the malformed-body guard). Add `_CONFLICT_RETRY_TYPE = "CONFLICT_RETRY"`.
- **FR2 — `create_item`:**
  - 2xx → `created=True, item=…`.
  - 409 with `error.details` → `created=False, existing=ExistingItemSummary(...), contended=False`.
  - 409 with `error.type == "CONFLICT_RETRY"` → `created=False, existing=None, contended=True`.
  - other / non-envelope / malformed 409 → original `HTTPError` re-raised.
- **FR3 — `add_ref`:**
  - 2xx → `added=True`.
  - 409 with `error.details` → `added=False, owner_work_item_id, owner_status, contended=False`.
  - 409 with `error.type == "CONFLICT_RETRY"` → `added=False, owner=None, contended=True`.
- **FR4 — DTOs:** `CreateItemResult` and `AddRefResult` each gain `contended: bool = False`
  (additive). `ExistingItemSummary.published` becomes `bool | None = None` (the details object
  may omit it — mirrors `details.get("published")`).
- **FR5 — queue-scoped `_by_ref` (① `55f9f1f5`) — a MUST-FIX, not an optimisation.** ① moved the
  ref lookup from the org-level `GET /items/_by_ref` (→ one item | 404) to queue-scoped
  `GET /{queue_id|'_'}/items/_by_ref` (→ a **list**). The old path no longer exists on new ①, so
  rc12's `get_item_by_ref` gets a router 404 and **silently** maps it to "no owner" (`None`) — ③'s
  same-content intercept gate then fails open. Replace it:
  - **Rename** `get_item_by_ref` → **`get_items_by_ref`** (clean, **no alias** — an alias would
    perpetuate the wrong "single owner" mental model; pre-GA rc, ③ is the sole consumer and changes
    in the same batch). Signature: `get_items_by_ref(organisation_id, *, queue_id: int | None = None,
    ref_type, ref_value) -> list[ItemResponse]`.
  - Path (**updated — ① then MERGED `_by_ref` into the paginated `/items`, 2026-07-21**):
    `GET /{queue_id|'_'}/items?ref_type=&ref_value=&s=1000` — passing the two ref params turns
    `/items` into the owner lookup; `queue_id=None` → `_` (whole org, `/items` accepts `_`); a queue
    id → that queue. The dedicated `_by_ref` route is gone (404). Parse the owner(s) from the paged
    envelope's `.items` (`_make_request`); owner count is bounded by one card per queue, so a single
    large page (`s=1000`) returns them all.
  - **200 + paged `{page, items}`**; empty `items` = no owner. **No 404→None branch** — a miss is an
    empty page, a genuine 404 propagates.
  - **Semantics:** the same `(ref_type, ref_value)` may be held by one card **per queue**; a 409's
    owner is always a **same-queue** owner. (create_item/add_ref/publish_item/get_item/delete_item
    call shapes and the conflict body are unchanged — the change is scope, not shape.)
  - Docs: retire the "org-wide UNIQUE key" / "org-scoped" wording (module header, create_item
    `external_refs` docstring, the by_ref docstring) → **queue-scoped**.
- **FR6 — name-resolution list endpoints (job-wiring by NAME, not hardcoded ids).** ③'s dispatcher
  is moving queue/template/folder out of the wheel `config` into the platform
  `schedule.static_input` (by name), resolving names→ids at runtime via CP list endpoints. The SDK
  adds the two list reads (both `?o=` + Page-wrapped `{page, items}`):
  - `AgencyWorkQueueClient.list(organisation_id, *, page=0, size=50) -> QueuesPagedResult` —
    `GET /api/work_queues` → `QueueResponse` {id, name, status, description?, created_on, modified_on,
    created_by?}.
  - **New delegate** `AgencySessionTemplatesClient` (`api_path="/api/session_templates"`, its own
    domain — not `/api/sessions`) + `AgencyClient.session_templates()`:
    `list(organisation_id, *, page=0, size=50) -> SessionTemplatesPagedResult` →
    `SessionTemplateResponse` {id, name, organisation_id, type?, executed?, audit_data?}.
  - Name→id matching (cache / not-found / duplicate → degraded) stays in ③'s dispatcher; the SDK
    provides the raw list only. ① CP unchanged. (Consumed via the editable link, ships with rc13.)
- **FR7 — release:** bump `0.0.1rc12 → 0.0.1rc13`; publish to PyPI (tag `v0.0.1rc13`). (The
  `_by_ref`→`/items` merge above landed during rc13 development, before any release, so it is just
  part of rc13 — no version was skipped.)

## Non-Functional Requirements

- **Public API stability for ③:** owner-known fields unchanged; `contended` defaults `False`,
  so existing ③ code is unaffected.
- **TDD:** offline stub tests reshaped from "flat top-level 409" to "envelope + `error.details`";
  add the `CONFLICT_RETRY` case (→ `contended=True`) and keep the malformed-409 re-raise.
- mypy strict / black / bandit clean; zero behavioural change to other delegates or methods.

## Acceptance Criteria

- **Unit (CI, stubbed):** create_item 409-with-details → `created=False`, existing populated,
  `contended=False`; create_item `CONFLICT_RETRY` → `created=False, existing=None, contended=True`;
  add_ref parallels both; malformed/non-envelope 409 → original `HTTPError` re-raised.
- **Live e2e (Gate A, CONDITIONAL on ①'s branch carrying the envelope + `CONFLICT_RETRY`):**
  create dup → owner read from `error.details`; exercise the `CONFLICT_RETRY` path if reachable.
- **rc13 published** to PyPI; ③ can bump its pin (no code change required of ③).

## Out of Scope

- ①'s server-side change (the envelope reshape + the new `CONFLICT_RETRY` type) and ③'s consumer
  (no change needed — API stable + additive flag).
- Any delegate/method other than `create_item`/`add_ref` conflict parsing.
- The session delegate and other work-queue methods.

## Branch

`feat/conflict-envelope-rc13`, created from `main` (at `cd88b10`).
