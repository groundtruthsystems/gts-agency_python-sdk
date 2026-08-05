# Spec — Gateway streaming + openai SDK integration

**Track:** `gateway_streaming_openai_20260707`
**Type:** Feature
**Branch:** `feat/agent-gateway-client`
**Context:** follow-up to `agent_gateway_client_20260706`, driven by CTO input (openai SDK viability
against the gateway, live-verified 2026-07-07) and the `stream=True` defect found live (the zero-dep
client blocks ~8 s buffering the SSE body, then fails with an unrelated `JSONDecodeError`).

## Overview

Three-part positioning (user decision, 2026-07-07):

1. `AgencyGatewayClient` stays the **zero-dependency built-in** (sync `requests`, rotation-automatic)
   and gains **native streaming** (SSE). Docs reposition it as the minimal option for consumers who
   do not want the `openai` package.
2. The SDK adds an **`[openai]` optional extra**: helpers on `AgencyGatewayClient` that return
   configured official openai clients (rotating bearer + `x-org` wired) — the **full-feature path**
   (streaming, tools, structured outputs, retries, async).
3. `docs/gateway.md` gains a "Using the official openai SDK" section (recipe + rotation patterns +
   streaming verified) and the tiered usage model.

Consumer mental model (single entry point, three tiers off the same `gw` object):

```
gw = client.gateway(org_id=..., gateway_base_url=...)   # auth/x-org/URL wired once
A) zero-dep:      gw.complete(...) / gw.complete_stream(...)          (no new deps)
B) full-feature:  gw.openai_client() / gw.async_openai_client()       ([openai] extra)
C) manual recipe: documented DIY openai.OpenAI(...) construction      (docs only)
```

## Functional Requirements

### FR1 — Native streaming + facade API fix (zero-dep)
- New DTOs in `gateway_dto.py` (all `extra="allow"`): `ChatDelta {role?: str, content?: str}`,
  `ChatChunkChoice {index: int = 0, delta: ChatDelta, finish_reason?: str}`,
  `ChatCompletionChunk {choices: list[ChatChunkChoice] = []}` (tolerates usage-only final chunks).
- `AgencyGatewayClient.chat_completions_stream(request) -> Iterator[ChatCompletionChunk]`:
  POSTs with `"stream": true` forced into the body, `requests.post(..., stream=True)`, parses SSE
  lines (`data: {...}`; stops at `data: [DONE]`), closes the HTTP response when the generator exits
  (including early exit).
- `AgencyGatewayClient.complete_stream(messages, model, **kw) -> Iterator[str]`: yields non-empty
  content deltas.
- **Defect fix (guard):** `chat_completions()` raises `ValueError` BEFORE any network call when the
  request carries a truthy `stream`, pointing to `chat_completions_stream()` / the openai path
  (product-guidelines: client-side validation fails fast as `ValueError`).
- **Facade API fix:** `AgencyClient.gateway(...)` signature changes to
  `environment: str | None = None`. Passing both `gateway_base_url` and `environment` raises
  `ValueError` before any network call (previously `environment` was silently ignored when a URL
  was given). When discovering with `environment=None`, it defaults to `"production"`. Semantics:
  **either give the URL, or give env (with discovery) — never both.**

### FR2 — `[openai]` extra (full-feature)
- `pyproject.toml`: `[project.optional-dependencies] openai = ["openai>=1.0"]`.
- `AgencyGatewayClient.openai_client(**kw) -> openai.OpenAI` and
  `.async_openai_client(**kw) -> openai.AsyncOpenAI`: `base_url = {gateway_base_url}/v1`,
  `default_headers = {"x-org": org_id}`, and an `http_client` carrying a per-request
  rotating-bearer httpx auth hook (reuse the observability `_HttpxBearerAuth` if import-clean,
  else a local minimal equivalent); `api_key` is a placeholder — real auth is the hook
  (per-request override verified live 2026-07-07).
- Lazy import + clear `ImportError` naming the `[openai]` extra when absent (observability
  precedent). Caller kwargs pass through (`max_retries`, `timeout`, ...).

### FR3 — Docs & example
- `docs/gateway.md`: tiered usage model (A/B/C table), "Using the official openai SDK" section
  (built-in helpers + manual recipe + three rotation patterns + streaming verified), native
  streaming section, URL-vs-environment config semantics (mutually exclusive), reposition the
  built-in client as the minimal no-extra-deps option.
- `README.md` gateway snippet + `CLAUDE.md` architecture updated.
- `examples/quick_gateway.py`: add a native-streaming step; add an openai-helper step that SKIPs
  cleanly when the extra is not installed.

## Non-Functional Requirements
- mypy strict / black 120 / bandit clean; coverage > 80% on new code.
- SDK core dependencies unchanged (`openai` only via the extra). No release in this track.
- Offline tests stub `requests`; `conftest.StubResponse` gains `iter_lines()` (additive);
  openai-helper tests use `pytest.importorskip("openai")`.

## Acceptance Criteria
- Live vs local `:4000`: `complete_stream` yields incremental deltas; the openai helper completes
  AND streams; `stream=True` into `chat_completions` fails fast with `ValueError` (no 8 s hang);
  `gateway(gateway_base_url=..., environment="test")` raises `ValueError`.
- Full offline suite green; all gates pass.
- **Ultracode addendum:** a multi-agent adversarial review (correctness / API-design / security
  lenses) of the new streaming + openai code passes before track close, confirmed findings fixed.

## Out of Scope
- Release (rc bump/tag) — post-merge. Async-native streaming in the zero-dep client.
- guideline-agent adoption (design doc Phase 2, separate repo/track).
- Deprecation decision for `AgencyGatewayClient` (explicitly retained per user decision).
