# Spec — Gateway: openai-SDK-only (remove the zero-dep fallback)

**Track:** `gateway_openai_only_20260707`
**Type:** Refactor (breaking, pre-1.0)
**Branch:** `feat/agent-gateway-client` (continues on the open PR #9; evolves in place before merge)
**Context:** follow-up to `agent_gateway_client_20260706` + `gateway_streaming_openai_20260707`,
driven by the user/CTO decision to **unify on one usage path**: drop the SDK's built-in
zero-dependency gateway client and route all gateway LLM traffic through the official `openai` SDK.

## Overview

The gateway capability collapses from three tiers to one. `AgencyGatewayClient` stops being an
HTTP/SSE client and becomes a thin **factory + wiring object**: it holds the shared credentials,
`org_id`/`x-org`, and resolved URL, and hands back configured official `openai` clients
(`openai.OpenAI` / `openai.AsyncOpenAI`) with the rotating bearer + `x-org` pre-wired. The zero-dep
`complete()`/`chat_completions()`/streaming surface and its hand-rolled DTOs are removed.

**Explicit decisions on record (user, 2026-07-07):**
- **`openai` becomes a CORE dependency** (moved to `[project.dependencies]`), not an optional extra.
  The user accepts that this pulls `openai` + its transitive deps (`httpx`, `anyio`, `jiter`,
  `distro`, `tqdm`, `sniffio`, ...) into *every* SDK install, including consumers that never touch
  the gateway — a deliberate departure from the SDK's previously lean core.
- This **reverses** the earlier "keep the zero-dep tier A" decision and deletes recently-hardened
  code (native SSE parsing, the ISO-8859-1 mojibake fix, the `stream=True` guard). Accepted:
  `openai` handles SSE natively and more correctly, and one code path is the goal.
- guideline-agent is **unaffected** — its Phase 2 adoption already targets `async_openai_client()`
  (see the SDK gateway consumption contract).

## Functional Requirements

### FR1 — `openai` as a core dependency
- Move `openai>=1.0.0` from `[project.optional-dependencies].openai` to `[project.dependencies]`;
  remove the now-empty `[openai]` extra.
- Remove `_require_openai()` and its lazy-import guard from `gateway_client.py` — `openai` is always
  importable now; `openai_client()`/`async_openai_client()` import it directly.
- Update install docs/commands to drop `[openai]` (dev install no longer needs the extra).

### FR2 — `AgencyGatewayClient` becomes an openai-client factory
- **Remove** `chat_completions()`, `complete()`, `chat_completions_stream()`, `complete_stream()`,
  the `_headers()` helper, and the `api_path` attribute if it falls dead (openai helpers build
  `{gateway_base_url}/v1` themselves).
- **Keep** `__init__`, `openai_client()`, `async_openai_client()`, `_httpx_bearer_auth()`
  (uses the shared `agency_sdk.auth_hooks`, from the M1 refactor — unchanged).
- Retain the `openai_client()` docstring notes (reserved kwargs, caller-owned lifecycle).

### FR3 — trim `gateway_dto.py` to discovery-only
- **Remove** `ChatMessage`, `ChatCompletionRequest`, `ChatChoice`, `ChatCompletionResponse`,
  `ChatDelta`, `ChatChunkChoice`, `ChatCompletionChunk` (openai owns these types now).
- **Keep** `AgentGatewayEnvironmentResponse`, `AgentGatewayStatusResponse` (URL discovery).
- Update the module docstring to reflect discovery-only scope.

### FR4 — facade unchanged in behavior
- `AgencyClient.gateway(*, org_id, gateway_base_url=None, environment=None)` keeps its identity cache,
  URL/environment mutual-exclusion, and `_discover_gateway_url` — it still returns the (now
  factory-only) `AgencyGatewayClient`. No fail-fast openai guard needed (openai is core).

### FR5 — docs, example, tests
- `docs/gateway.md`: collapse the tier table; the openai helper is *the* path. Keep a short
  "DIY / advanced" note for building your own openai client via `agency_sdk.auth_hooks` (former
  tier C). Remove the tier-A built-in sections.
- `examples/quick_gateway.py`: rewrite to use `openai_client()` / `async_openai_client()` only —
  one-shot, streaming (openai `stream=True`), and the wrong-`x-org` → 403 negative
  (`openai.APIStatusError`, status 403).
- `README.md` gateway snippet + `CLAUDE.md` architecture + `conductor/tech-stack.md` synced.
- **Delete** `test_gateway_client.py`, `test_gateway_streaming.py`, and the chat-DTO tests in
  `test_gateway_dto.py` (keep discovery-DTO tests). Remove the missing-`[openai]`-extra guard test in
  `test_gateway_openai.py`. `test_gateway_facade.py` (discovery/cache) stays unchanged.

## Non-Functional Requirements
- mypy strict / black 120 / bandit clean; coverage > 80% on the retained code.
- No release in this track (rc bump/tag is post-merge, as before).

## Acceptance Criteria
- `pip install gts-agency-python-sdk` (no extra) makes `client.gateway(...).openai_client()` work.
- Removed symbols (`complete`, `complete_stream`, `chat_completions[_stream]`, the chat DTOs,
  `_require_openai`) are gone; a grep for them in `agency_sdk/` (excluding history) is clean.
- Live E2E vs local `:4000`: `openai_client()` one-shot + streaming, `async_openai_client()` one-shot,
  and wrong-`x-org` → `openai.APIStatusError` 403 all pass.
- Full offline suite green; all gates pass; docs/example contain no tier-A references.

## Out of Scope
- Release / version bump / tag (post-merge).
- guideline-agent adoption (separate repo; already targets `async_openai_client()`).
- Reintroducing any zero-dep path (explicitly removed).
