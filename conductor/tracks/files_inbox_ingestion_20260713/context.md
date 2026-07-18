# Cross-repo context — AgencyWorkQueueClient (Track ②, gts-agency_python-sdk, the DELEGATE)

> Persisted 2026-07-13 so any fresh session in this repo has self-contained context without opening
> the other two repos. This repo adds a **thin typed client** over gts-agency's new
> `/api/work_queues` ingestion surface; it **mirrors ①'s contract** and is consumed by ③.
> Rewritten 2026-07-16: the `_command` contract question is resolved (this repo conformed,
> `95b2e8b`), P2+P2b are checkpointed, and **Phase 3A is DONE — the live e2e passed against ①'s
> branch with zero fixes needed anywhere**. All that remains here is 3B (Gate B: publish rc12).
> Update 2026-07-17: a **second, independent delegate — `AgencySessionClient` (attach/update, no
> register) — was BUILT on this same branch** (`fa542f3`), per the user; see the Session inheritance
> section at the bottom. **Release guardrail: it must NOT be published in the files-inbox rc12.**

## What this is

The guideline-agent files-inbox ingestion consumer (③) needs a typed SDK client for the new
work-queue ingestion endpoints (created by gts-agency Track ①). This track adds
`AgencyWorkQueueClient(BaseDelegateClient)` following the `AgencyFilesClient` pattern, plus a
`work_queues()` facade accessor, then publishes a new rc for ③ to lock onto.

## The 3-repo effort (unified track name `files_inbox_ingestion_20260713`)

| Track | Repo | Branch | Status (2026-07-16) |
|---|---|---|---|
| ① | gts-agency | `feat/work-item-external-ref` | **Code-complete, running on localhost:13001, and its contract is now PROVEN over the wire by this repo's 3A.** Phases 1–3 checkpointed (`…`, `7951daf4`) · **Phase 4 Stages 0+1 DONE** (Stage 1 = hosting this repo's e2e; it needed **zero fixes**). **Stage 2 = ③'s full chain** is next |
| ② | **this repo** | `feat/work-queue-delegate` | P1 done (`c5e8a89`); **P2+P2b DONE (`0318a6d`)**; rc12 bumped (`a982736`, not published); **P3A local e2e DONE — ALL STEPS PASSED vs `localhost:13001`, checkpoint `8502a49`** (both flat 409s evidenced live; `examples/quick_work_queue.py`). **NEXT: P3B — gated on ① merged + deployed to the SHARED platform** (re-e2e there → publish rc12). **ALSO on this branch: `AgencySessionClient` BUILT (`fa542f3`)** — separate capability, must NOT ship in rc12 (own rc, Gate B) |
| ③ | gts-guideline-agent | `feat/files-inbox-ingestion` | Phases 1–4 done (incl. an LLM-only classifier rework, FR8–FR11); Phase 4 checkpointed `bb55a6f`; **its side of the contract fix is already done** (`e5b8c78`). PAUSED — its Phase 5A runs **after** this repo's 3A, consuming THIS repo as a **local editable install** (no publish needed — that is Gate B); only its 5B needs the published rc12. One pre-flight item: ontology is docker-network-only, so 5A must publish that port if it runs natively |

**Order — TWO GATES (2026-07-16; agency is a shared platform, nothing outward until the full
chain produces real output locally):** Phases 1–2b are done stub-first. **Gate A (Phase 3A):**
①'s feature BRANCH runs **natively** on `localhost:13001` (the docker-image path was reverted;
①'s plan Phase 4 Stage 0 — **DONE**) → this repo's live e2e runs against LOCAL; ③ consumes this repo as a **local
editable install** (`[tool.uv.sources]` — the observability-track precedent `3912d6a`→`1dd91fa`),
so NO publish is needed yet, and anything surfaced gets fixed on ①'s branch before it merges.
**Gate B (Phase 3B):** local full chain green → ① merges + deploys → re-run the e2e against the
SHARED deployment → **publish rc12** (the outward act) → ③ swaps editable→pin.
All three repos are local-only feature branches — nothing pushed, no PRs; pushes/PRs/publish are
Gate-B actions needing the user's explicit go.

**Note on the version bump:** `0.0.1rc12` is already in `pyproject.toml` (`a982736`, user-instructed
ahead of the e2e). Bumping is local and harmless; **publishing is the outward act and stays in 3B**.
Do not read the bumped version as "released".

**⚠️ Publish blocker — `uv.lock` self-version drift (re-survey 2026-07-17):** `pyproject.toml` is
`0.0.1rc12` but the lock's self-package entry `uv.lock:269` is still `0.0.1rc9`
(`source = { virtual = "." }`). Re-lock (`uv lock`) so the self-version matches **before/at** the 3B
publish, or the released artifact's own version metadata will be stale. Deliberately NOT re-locked
now — a full `uv lock` re-resolves every dep (noisy + needs re-test), so it belongs to the Gate-B
publish step, not to local doc hygiene.

## ✅ Phase 3A DONE (2026-07-16, checkpoint `8502a49`) — the contract held on first contact

```
Stage 0  ① stack up from its BRANCH       ✅ DONE
Stage 1  ② THIS e2e                       ✅ DONE — ALL STEPS PASSED, zero fixes needed on ①
Stage 2  ③ full chain (minutes, real LLM) ⬅ NEXT — the chain's next action
Stage 3  ① rebase → re-test → PR
```

**What it proved** (`examples/quick_work_queue.py` driving this delegate vs ①'s native branch on
13001; evidence in the `4f77ae6` git note):

- **CROWN JEWEL — both 409s came back FLAT, not enveloped.** create's
  `{'work_item_id': 6, 'status': 'backlog', 'published': False}`; add_ref's narrower
  `{'work_item_id': 6, 'status': 'backlog'}`. **This is the thing no unit test could prove** — every
  repo stubs the flat shape — and it is why Stage 1 exists. ① honours the contract; **no fixes.**
- org-scoped `_by_ref` resolved an owner in ANOTHER queue (cross-queue, no queue id) ✓ · missing
  ref → `None` (404 mapped) ✓ · item DELETE → `_by_ref` then `None` (refs CASCADE'd) ✓
- **Both residual risks confirmed immune as designed**: create returns 200 not 201 (any 2xx =
  created); delete returns 204 empty (the delegate ignores the body).
- Wire openapi binds the 409s to `ItemConflictResponse`/`AddRefConflictResponse` and 200 `_command`
  to `ItemCommandResponse {success,message,session_id?}` — **exactly matching Phase 2b**.
- Idempotent: 2 throwaway queues, unconditional teardown, post-run `total:0` (no residue).

**The one observation, deliberately NOT worked around here:** `publish` dispatches a real session
and the dispatch blocked past the 30s client timeout (ReadTimeout). That is the session subsystem
reaching the LLM/agentgateway — **Stage-2 / ③ territory, not this delegate's wire contract**
(`ItemCommandResponse` parsing is proven by unit tests against the exact server schema and
transitively by the live 200 on add_ref). Per the Gate-A rule, no client-side workaround was added.
**③'s whole flow is publish→session→worker, so this is the first wall Stage 2 meets.**

**What is running, and how to reach it** (details in ①'s plan Phase 4 Stage 0a):

- **`http://localhost:13001`** — ①'s branch running **natively** (`cargo run`, NOT a docker image;
  that path was built then reverted). It is genuinely the branch, verified over the wire: openapi
  lists `/api/work_queues/items/_by_ref` and both 409s bound to
  `ItemConflictResponse`/`AddRefConflictResponse`.
- **Auth = Keycloak** `client_credentials` (the `agency-system` client) → `Authorization: Bearer
  <jwt>`. **org 2** works.
- Deps (MySQL/Valkey/MinIO/Keycloak) come from the sibling `gts-local-environment` stack.
- ① already smoke-verified the surface over the wire in Stage 0b — **flat 409, no envelope,
  confirmed live**. This e2e is the systematic version of that, driven through THIS delegate.

Two things to hold onto when 3A runs:

- **Findings are fixed on ①'s BRANCH, never worked around here.** The loop is: surface → fix on ①
  → `cargo run` restart (seconds — native, no image rebuild) → re-run. This track still goes first
  because ③'s stage costs a 10-minute DBQ extraction per attempt, and contract bugs should die
  in front of that, not inside it.
- **Stage 1 is where the flat 409 first leaves the unit tests.** `_conflict_body` expands the JSON's
  top-level keys straight into a pydantic model, so an error envelope from ① surfaces here as a
  loud `ValidationError`/`KeyError` — and nowhere earlier, because every repo's unit tests stub the
  flat shape. **Assert the shape; do not defensively parse around an envelope** (an envelope means
  ① violated the contract, and the right reaction is a red test, not compatibility).

## ⚠️ THE RESOLUTION (2026-07-16): commands return `ItemCommandResponse` — this repo must conform

What this context previously called "e2e risk #1 / the designed drift alarm" is **resolved, in the
server's favour**, and the alarm framing itself was the mistake (detection is not a decision, and
that alarm would have fired at live e2e, after ① deployed — the most expensive place to learn it).

- **History:** the design's §6 declared `item_command -> ItemResponse` for an endpoint that
  ALREADY returned `ItemCommandResponse {success, message, session_id}` (`work_queue_dto.rs:131`).
  That unverified line was called "frozen", and this delegate faithfully built on it — commands
  here still parse `ItemResponse(**body)`.
- **Why the server wins:** gts-agency owns `/api/work_queues`; an unverified line in a consumer's
  design doc does not reshape a server API. And nothing consumes the body anyway — ③'s dispatcher
  calls `publish_item(...)` as a bare statement, never calls `item_command` at all, agency-web
  discards it. (`ItemCommandResponse` is also a *considered* shape for an action endpoint: did it
  work, what happened, the id of the session it created. Want the item? GET it.)
- **Per-repo state:** ① plan corrected (`ee84c579` — no reshape; new commands `add_ref`/`retry`/
  `reprocess` adopt the established response). ③ already conformed (`e5b8c78`) — its Protocol
  returns `ItemCommandResponse` and **dropped `item_command` entirely** (the agent never calls it;
  ops actions go via API/CLI/board). **② CONFORMED (`95b2e8b`, 2026-07-16):** `ItemCommandResponse`
  DTO added (transcribed, per §6.0), `publish_item`/`item_command` retyped, tests stub the real
  server body. All three repos now agree.
- **The recurrence guard — design §6.0 (in ③'s design doc):** every §6 endpoint is marked NEW vs
  EXISTING. An EXISTING endpoint's shape is **transcribed from the server, never asserted** — this
  repo already did that correctly once (`ItemResponse`'s field list was transcribed verbatim from
  `work_queue_dto.rs`), which is why get_item is right and the command response was the one miss.

## The contract to mirror (owned by ①; §6.0 is the provenance registry)

Base URL + `/api/work_queues`. All calls carry `?o={org}`.

- `create_item(queue_id, org, *, title, session_template_id, input_data, external_refs=None,
  metadata=None)` → 2xx item; **409 → FLAT `{work_item_id, status, published}`** (a card already
  owns a ref; the TX rolled back so no card was created). Flat domain object, NOT the standard
  error envelope — ① is pinned to the double-result handler pattern for exactly this reason, and
  `_conflict_body` here does `response.json()` and expands the keys directly.
- `POST /{queueId}/items/{itemId}/_command {command}` → **`ItemCommandResponse
  {success, message, session_id?}` for ALL commands** (the endpoint's EXISTING shape; new commands
  adopt it):
  - `add_ref {ref_type, ref_value}` → 2xx added; **409 → flat owner `{work_item_id, status}`**
    (narrower than create's — no `published`).
  - `publish` / `unblock` / `retry` / `reprocess` → `ItemCommandResponse`.
- `DELETE /api/work_queues/{queueId}/items/{itemId}` → full forget (CASCADEs the refs).
- `GET /api/work_queues/items/_by_ref?o=&ref_type=&ref_value=` → item | 404 — **org-scoped** (the
  server UNIQUE key is org-wide).
- `GET /{queueId}/items/{itemId}` → `ItemResponse` (field list transcribed verbatim from the
  server DTO; nullable fields are omitted when null — `skip_serializing_if`).

## This repo's scope, surface & the ONE gotcha

Files: `agency_sdk/delegates/work_queue_dto.py` + `work_queue_client.py`; registered in
`agency_sdk/client.py` (`work_queues()` accessor). Delegate methods: `create_item`, `publish_item`,
`add_ref`, `get_item_by_ref` (org-scoped, 404→None), `get_item`, `item_command` (kept here — ops
callers need it even though the agent's Protocol dropped it), `delete_item`.

**THE GOTCHA — 409 as control flow:** `BaseDelegateClient._make_request` calls `raise_for_status()`
(`base_client.py`), so a 409 raises `requests.HTTPError`. `create_item` and `add_ref` MUST catch
the 409 and return a typed result (`created=False` / `added=False` + owner fields), never re-raise.
For all other calls a 409 is a genuine error and propagates.

## Progress & development log

- **Phase 1 (DTOs) DONE** — `work_queue_dto.py` + 7 DTO tests (`8b1921e`), checkpoint `c5e8a89`
  (user-confirmed). Nullable `ItemResponse` fields default `None`; payload fields typed `Any`.
- **Phase 2 (delegate + facade) DONE** — `AgencyWorkQueueClient` + 20 protocol tests
  (`0d6ce3d`), facade (`d175ee1`). Combined Phase 2+2b checkpoint **`0318a6d`** (user-confirmed,
  2026-07-16) carries the README/CLAUDE.md doc sync; verification report in its git note.
- **Phase 2b (conform command return types) DONE** — `95b2e8b` (2026-07-16, red-first):
  `ItemCommandResponse {success, message, session_id?}` DTO; `publish_item`/`item_command` retyped;
  command tests stub the real server body. `add_ref` needed no change (already ignored the 2xx
  body). Checkpointed with Phase 2 (`0318a6d`).
- **Phase 3A (local live e2e) DONE** — `examples/quick_work_queue.py` vs ①'s branch on
  `localhost:13001`: **ALL STEPS PASSED**, checkpoint `8502a49`. Both flat 409 bodies evidenced live
  (create `{work_item_id,status,published}`, add_ref `{work_item_id,status}` — NOT the error
  envelope); org-scoped cross-queue `_by_ref`; delete CASCADE forget. Residual risks confirmed immune
  (create 200-not-201, delete 204). publish dispatch is Stage-2 (blocked >30s, tolerated by the e2e);
  user chose to keep ItemCommandResponse parse unit+schema-verified rather than chase it. Idempotent —
  post-run `total:0`. Evidence in the `4f77ae6` + `8502a49` git notes.
- **Phase 3B NOT started** — gated on ① merged + deployed to the SHARED platform. Version already
  bumped to `0.0.1rc12` (`a982736`, not published — publishing is the Gate-B outward act). 3B =
  re-run the e2e vs the shared deployment → publish rc12 → ③ swaps editable→pin.
- Gates at last run: 152 passed; work_queue files 100% coverage; mypy strict / black / bandit clean;
  e2e script black + mypy clean. Per-task details in git notes (`git log --show-notes`).
- **`AgencySessionClient` (separate capability) BUILT 2026-07-17** — `fa542f3` (code) + `10b3a3c`
  (docs). `session_dto.py` (`SessionStatus`, `SessionCommandResponse` transcribed, `AnalyticsEvent`
  promoted) + `session_client.py` (`attach`+`update` only, **no `register`**) + `.sessions()` accessor.
  **base_client retry fix (adversarial-review-driven):** folding a blanket retry into the shared base
  would auto-retry non-idempotent POSTs — a post-commit `ConnectionError` reset would double-send
  `create_item`/`add_ref`, re-hit the UNIQUE key, and mis-report a WON claim as lost (409). Fix:
  auto-retry only reads (GET/HEAD/OPTIONS); writers opt in (`update` does — idempotent + ①'s
  monotonicity guard); ReadTimeout + status codes never retried. Gates: 176 passed, new modules 100%
  cov, mypy/black/bandit clean; `update` wire live-smoked (404 on bogus session); work-queue 3A e2e
  re-run still ALL STEPS PASSED (no regression from the shared-base change). See the Session
  inheritance section for the full story. **RELEASE guardrail: keep it out of the files-inbox rc12.**

## Residual e2e risks (after Phase 2b resolves the big one)

1. Create returns 200 on today's server vs 201 in the contract — the SDK treats any 2xx as
   created, immune either way.
2. `delete_item` ignores the response body — immune to `{"success": true}` vs 204.

## Pointers

- Design of record (in gts-guideline-agent): `docs/dbq/files-inbox-ingestion-design-20260712.md`
  — **v4**; read §6.0 (endpoint provenance) before changing any signature here.
- Track spec/plan: [spec.md](./spec.md) · [plan.md](./plan.md) (Phase 2b is the next task).
- Miro: https://miro.com/app/board/uXjVH8LgO2E=/

## Stage 2 update (2026-07-17): the delegate ran inside a real worker deployment, and the 30s block is cleared

**The publish-dispatch block this repo saw in 3A (deferred to Stage 2) is root-caused and gone —
and it was NOT a wire-contract issue, which is exactly why 3A logged it as an observation.** It was
an environment break: dispatch builds a fresh redis pool per publish from the worker-group config
(`host: "redis"`), and the control plane was running **natively on the Mac**, where `redis` does not
resolve → `redis.rs:20` `.unwrap()` on a pool build whose r2d2 connect timeout is 30s → panic → the
HTTP request hangs 30s → this repo's client hit its ReadTimeout. Master's code, not ①'s. Cleared by
aliasing `redis` in `/etc/hosts`; publish then returns in **0.05s** with the expected
`ItemCommandResponse{success, message, session_id}`.

**This delegate was consumed all the way through the real platform-worker path** (Track ③ Phase 5A,
the production shape — not just the branch wire it passed in 3A):
- built as a wheel (`uv build --wheel` on `feat/work-queue-delegate` → `gts_agency_python_sdk-
  0.0.1rc12-py3-none-any.whl`), **vendored** into ③'s deployment tarball, and installed
  **non-editably** inside the worker container (`uv pip install` — the editable `[tool.uv.sources]`
  path cannot resolve there). Confirmed in the worker's venv: `gts_agency_python_sdk-0.0.1rc12`.
- `create_item` (with `external_refs`), `publish_item`, `add_ref`, and DELETE all drove the real
  dispatcher; both flat 409 bodies and the `_by_ref` lookup were exercised live under load (the
  8-doc matrix). So the delegate is proven **inside a running agent**, not only against ①'s branch.

**No delegate change needed for the "failed → Done" board bug (corrected 2026-07-17).** An earlier
note here called `get_item` load-bearing for a fix that had the agent read its dispatched
`session_id`. That framing was wrong: the card lifecycle is worker-driven (the worker's
complete/error terminal event, keyed on the dispatched session id it already holds, decided by the
agent's process exit code). The real fix is entirely in **③** — the agent exits non-zero on a
pipeline failure so the worker emits `agency.worker.error` → card Blocked. This delegate is not
involved. (`get_item` remains correct and exposed; it is simply not part of that fix.)

**Phase 3B unchanged — still Gate-B gated:** re-run the e2e against the SHARED deployment, then
publish rc12. Bumping happened (`a982736`); PUBLISHING has not, and must not until ① is merged and
deployed.

---

## Session inheritance (CTO decision 2026-07-17): assessed → shared `AgencySessionClient` now BUILT

**Decision:** the agent inherits the dispatched session_id instead of self-registering its own
control-plane session (kills the orphan "leg ②"). Implemented across ①(worker inject `event.id()`
into agent arguments — DONE `222d25e3` on gts-agency) + ③(agent adopts `arguments.session_id` —
pending). **② is NOT on this path.**

**Verified (survey 2026-07-17):** this SDK has **no session-reporting delegate** — no
`register_session` / `update_session` / `session_templates/_command` / `SessionEventPublisher`
anywhere in `agency_sdk`. The agent's session register/update is **bespoke in ③**
(`guideline_agent/events/control_plane_client.py`), which imports nothing from `agency_sdk`. This
SDK's `work_queue` delegate is **dispatch-side only** (create/publish cards); the `session_id` it
returns in `ItemCommandResponse` is the dispatched session id (informational for the dispatcher).
`session_vault_client.py` is a per-session secret K/V store (`/api/sessions/{id}/vault`), unrelated.

**FUTURE (out of scope for files-inbox) — NOW DECIDED (2026-07-17):** because ③'s session-reporting
client is bespoke and this SDK offers none, the "inherit / never self-register" policy would be
re-implemented per agent. **Decision: build a shared `AgencySessionClient` delegate in THIS SDK**
(Option B — ③ adopts it, no throwaway bespoke code). The delegate exposes **only `attach` +
`update`** (NOT `register` — exposing self-register is a footgun that reintroduces the orphan
session); it reuses the shipped OAuth/plumbing, folds retry into `BaseDelegateClient`, and promotes
the `AnalyticsEvent` DTO. Design of record: [`docs/session_reporting_delegate_design.md`](../../../docs/session_reporting_delegate_design.md).
It is a **separate capability from files-inbox**; ①-server needs nothing (register/update commands
are live, and ① already injects `session_id` at `222d25e3`). Sequencing: ② build → ③ adopt vs editable
dep + prove inject→attach→update locally (Gate A) → ② publish + ③ pin (Gate B).

**BUILT 2026-07-17 (on THIS `feat/work-queue-delegate` branch, per the user's explicit instruction —
it did NOT get its own branch).** Shipped: `session_dto.py` (`SessionStatus` -1/0/2,
`SessionCommandResponse` transcribed from the live server, `AnalyticsEvent` promoted verbatim from ③),
`session_client.py` (`attach` + `update` only — **no `register`**), `.sessions()` accessor, and the
base-client connection-retry. **Retry was gated to reads after an adversarial review caught a real
bug:** a blanket per-delegate retry would auto-retry non-idempotent POSTs, and a post-commit
`ConnectionError` reset would double-send `create_item`/`add_ref` → re-hit the UNIQUE key → mis-report
a WON claim as lost (409). Fix: auto-retry only GET/HEAD/OPTIONS; writers opt in (`update` does, safely
— idempotent + ①'s monotonicity guard). 176 offline tests green; mypy/black/bandit clean; `update`
wire live-smoked vs ①'s branch (server 404s on a bogus session → envelope accepted); the work-queue
3A e2e still ALL STEPS PASSED (now incl. publish, the redis alias being in place). **RELEASE guardrail
stands: this must NOT be published in the files-inbox rc12** — it needs its own rc (Gate B), and ③'s
adoption (delete the bespoke `control_plane_client.py`; `attach`+`update`) is still pending.

## ③ adopted + Gate-A verified (2026-07-18)

Update to "③'s adoption … is still pending" above: **③ HAS adopted the delegate** (Phase 9, in
gts-guideline-agent — the bespoke `control_plane_client.py` is deleted). `_configure_event_channels`
builds `build_agency_client(cp).sessions()` + `attach(session_id)`; the publisher drives
`sessions.update(org, status=<int>, events/result/metrics)` (the status int map lives here in the SDK;
③ owns the pipeline→status decision).

**VERIFIED LIVE (Gate A, 2026-07-18)** on the local docker stack with ① run natively + ③ agent v0.5.3
vendoring THIS wheel: the agent's session events / result / metrics — and the bounded terminal error on
a failed run — all land on the inherited leg-① session via `AgencySessionClient.update` (the S3 session
payloads are its output). Session inheritance, Phase 7, Issue 1/2, and Phase-10 ingestion events all
confirmed. Full runbook + evidence in ③:
`gts-guideline-agent/docs/dbq/files-inbox-ingestion-local-verification-20260718.md`.

**RELEASE guardrail STILL STANDS (Gate B):** this delegate must NOT ride the files-inbox rc12 — it
needs its own rc, after which ③ pins the published SDK (dropping the editable `[tool.uv.sources]`).
