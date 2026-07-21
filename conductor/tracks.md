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

- [~] **Track: Conflict-body envelope parsing (work-queue 409) → rc13**
*Link: [./tracks/conflict_envelope_20260718/](./tracks/conflict_envelope_20260718/)*

**Status:** In Progress (2026-07-20) — **all SDK work DONE + ③-integrated; only the rc13 publish (Gate B) remains.** SDK-side mirror of ①'s work-queue 409 reshape: flat body → standard `{error:{message,type,details}}` envelope + `CONFLICT_RETRY` for the owner-less fallback (`create_item`/`add_ref` read owner from `error.details`, additive `contended` flag; public API to ③ unchanged). Checkpointed phases: **P1 envelope parsing + DTOs** (`6ed3dcf`) · **P1.5 queue-scoped `_by_ref`** (`get_item_by_ref`→`get_items_by_ref` list, MUST-FIX for ① `55f9f1f5`; `e212a33`) · **P1.6 name-resolution list endpoints** (`work_queues().list` + new `session_templates().list` so ③ wires queue/template by NAME from `schedule.static_input`, not hardcoded ids; `e1db2ba`) · **P2 Gate-A local e2e** (ALL STEPS PASSED vs ①, zero SDK fixes; `3453278`) · **P1.7 `_by_ref`→`/items` merge** (① follow-up 2026-07-21: `get_items_by_ref` retargeted to the paginated `/items?ref_type=&ref_value=&s=1000`, parses `.items`; `_by_ref` route removed; live e2e re-run ALL STEPS PASSED incl. publish). Bumped to **rc13** (the merge landed during rc13 dev, pre-release — no version skipped; rc13 was never published, so it is reused); local wheel + editable link for ③'s local e2e. **① server work is out of scope**. **Next: Phase 3 publish — PR #12 (rc13) open; tag `v0.0.1rc13` → PyPI is the Gate-B act (explicit go).**
**Branch:** `feat/conflict-envelope-rc13` (from `main` `cd88b10`).
