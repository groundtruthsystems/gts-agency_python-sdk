# Project Tracks

This file tracks all major tracks for the project. Each track has its own detailed plan in its respective folder.

---

- [x] **Track: Observability tracing integration into the SDK (research, design, and implementation)**
*Link: [./tracks/observability_tracing_20260617/](./tracks/observability_tracing_20260617/)*

---

- [x] **Track: Agent gateway client — OpenAI-compatible LLM routing via client.gateway(...) (SDK side, design doc Phase 1)**
*Link: [./tracks/agent_gateway_client_20260706/](./tracks/agent_gateway_client_20260706/)*

---

- [x] **Track: Gateway streaming + openai SDK integration — native SSE in the zero-dep client, [openai] extra full-feature helpers, tiered usage docs**
*Link: [./tracks/gateway_streaming_openai_20260707/](./tracks/gateway_streaming_openai_20260707/)*

---

- [x] **Track: Gateway openai-SDK-only — remove the zero-dep fallback, openai becomes a core dependency, AgencyGatewayClient becomes an openai-client factory**
*Link: [./tracks/gateway_openai_only_20260707/](./tracks/gateway_openai_only_20260707/)*

---

- [~] **Track: AgencyWorkQueueClient — work-queue SDK delegate for files-inbox ingestion**
*Link: [./tracks/files_inbox_ingestion_20260713/](./tracks/files_inbox_ingestion_20260713/)*

**Status:** In Progress (2026-07-16) — **all stub-testable work is DONE; waiting on nothing of its own.** Phase 1 (DTOs) checkpointed `c5e8a89`; **Phases 2 + 2b checkpointed together (`0318a6d`, user-confirmed)** — delegate + facade + the command-shape conform (`95b2e8b`): commands return the server's real `ItemCommandResponse {success, message, session_id}`, resolving the cross-repo `_command` contract question in the server's favour, and all three repos now agree (full story in the track `context.md`). Version already bumped to **rc12** (`a982736`, user-instructed ahead of the e2e) — **bumping is local; PUBLISHING is the Gate-B act and has not happened**. **Phase 3A DONE — checkpoint `8502a49`, ALL STEPS PASSED vs ①'s branch on `localhost:13001`, zero fixes needed anywhere**: `examples/quick_work_queue.py` drove this delegate through the full lifecycle and evidenced **both flat 409 bodies live** (the crown jewel — no unit test can prove it, since every repo stubs the flat shape), plus org-scoped cross-queue `_by_ref` and DELETE→CASCADE. One observation deferred to Stage 2: publish's session dispatch blocked past the 30s timeout (③'s territory, not this wire contract). **Next: Phase 3B — Gate-B gated** (re-e2e vs the shared deployment → publish rc12). **Track ② of the 3-repo `files_inbox_ingestion_20260713` effort** (server ① in gts-agency — **Phases 1–3 checkpointed and its Gate-A Stage 0 DONE**: the branch runs natively on 13001 and is now waiting on this track's 3A; main track ③ in gts-guideline-agent — Phases 1–4 done incl. the LLM-only classifier rework, paused, and it consumes THIS repo as an editable install for its local Phase 5A). **Two-gate order (2026-07-16):** Phase 3A live e2e runs against ①'s branch running NATIVELY on localhost:13001 (no merge, no image needed; ③ consumes this repo as an editable install meanwhile); Phase 3B — re-e2e vs the shared deployment + the **rc11 → rc12 publish** — is gated on ① merged + deployed, and ③'s pin swap is gated on that rc. **Update 2026-07-18:** the Stage-2 "30s publish block" was root-caused (native-CP→container `redis` DNS, an env issue ① fixed) — NOT this wire contract. The full 3-repo chain re-verified LIVE on the native stack: this work-queue delegate drove the ingestion fan-out, and the **session-reporting delegate `AgencySessionClient`** (a separate capability built on this same branch, `fa542f3` — attach/update, no register) is now ADOPTED by ③ (its Phase 9) + verified (agent events/result/metrics + the failed-run bounded error flow through `sessions.update`; see the ② context.md's "③ adopted + Gate-A verified" section). Runbook + matrix: `gts-guideline-agent/docs/dbq/files-inbox-ingestion-local-verification-20260718.md`. **Update 2026-07-18 — PR #10 MERGED to `main`** (merge commit `86cb337`; Hermes review addressed in `7ed7625`). Both delegates (work-queue + session) are now on `main`, and `pyproject` there is rc12. **No publish happened** — merging does not publish (only a pushed `v*` tag does); the highest published release is still **rc11** (2026-07-07), no rc12 tag/run exists. **Publish remains Gate-B gated** (① deployed to the shared platform → re-e2e → tag `v0.0.1rc12`, explicit go). **Guardrail note (overtaken by the merge):** the earlier "session delegate must get its own rc, separate from rc12" is now moot — the merge co-located both delegates on `main`, so an rc12 cut from `main` will bundle **both**; that is accepted (③ has already adopted `AgencySessionClient` and Gate-A-verified it). The `uv.lock` "blocker" was a misconception (gitignored, absent from the wheel — the version comes from `pyproject`). **Track stays In Progress:** only the Gate-B publish (Phase 3B) is left.
**Branch:** `feat/work-queue-delegate`
**Track date source:** Created 2026-07-13 from main for this track (branch-aligned).
**Why:** The guideline-agent files-inbox ingestion consumer needs a typed client over the new `/api/work_queues` ingestion surface (create-item-with-refs, add_ref, org-scoped _by_ref, unblock/retry/reprocess, item delete) with 409-as-control-flow. No work-queue delegate exists in the SDK today.
**Scope:** Python SDK ONLY, additive: `AgencyWorkQueueClient(BaseDelegateClient)` + `work_queue_dto.py` (CreateItemResult/AddRefResult/ItemResponse) + facade `work_queues()` accessor + stub-based tests; version bump + rc publish. Mirrors the ① contract reshaped by ①'s Phase 0 (retry command + item DELETE + richer 409 bodies). Design of record: guideline-agent `docs/dbq/files-inbox-ingestion-design-20260712.md` §6. **Out of scope:** server implementation (①), agent code (③), delegate methods not needed by ingestion (board/comments/dependencies/transitions).
