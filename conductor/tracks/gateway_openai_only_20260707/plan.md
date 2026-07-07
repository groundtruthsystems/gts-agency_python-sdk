# Plan — Gateway: openai-SDK-only (remove the zero-dep fallback)

Methodology: per `workflow.md` (tests drive the change), >80% coverage on retained code,
mypy strict / black / bandit gates, Phase Completion Verification Protocol per phase.
Removal-heavy: each phase's "test" step asserts the removed surface is gone AND the retained
surface still works, rather than red-before-green on new behavior.

## Phase 1: openai as a core dependency [e04f24e]

- [x] Task: Adjust tests + packaging for openai-as-core (e04f24e)
    - [x] Remove the missing-`[openai]`-extra guard test in `test_gateway_openai.py`
    - [x] `test_gateway_openai.py` imports `openai` as a core dep directly (a missing core dep fails module import); helpers still build
    - [x] `pyproject.toml`: moved `openai>=1.0.0` to `[project.dependencies]`; dropped the `[openai]` extra
    - [x] Removed `_require_openai()` + calls from `gateway_client.py`; helpers import `openai` directly
- [x] Task: Verify — full suite green (121 passed); mypy / black / bandit clean (e04f24e)
- [x] Task: Conductor - User Manual Verification 'Phase 1' — folded into the continuous run (user: "一直实现直至完成 e2e") (e04f24e)

## Phase 2: Strip the zero-dep surface [e04f24e]

- [x] Task: Remove tier-A methods from `gateway_client.py` (e04f24e)
    - [x] Deleted `chat_completions`, `complete`, `chat_completions_stream`, `complete_stream`, `_headers`, `api_path`
    - [x] Kept `__init__`, `openai_client`, `async_openai_client`, `_httpx_bearer_auth`
    - [x] Deleted `test_gateway_client.py` and `test_gateway_streaming.py`
- [x] Task: Trim `gateway_dto.py` to discovery-only (e04f24e)
    - [x] Deleted the 7 chat/chunk DTOs; kept `AgentGatewayEnvironmentResponse` / `AgentGatewayStatusResponse`
    - [x] Pruned `test_gateway_dto.py` to discovery-DTO tests; updated imports + docstring
- [x] Task: Verify — facade/openai/discovery tests green; residual-symbol grep clean; mypy / black / bandit (e04f24e)
- [x] Task: Conductor - User Manual Verification 'Phase 2' — folded into the continuous run (e04f24e)

## Phase 3: Docs, example & gates [checkpoint: acce36a]

- [x] Task: `docs/gateway.md` — collapsed to the single openai path (+ DIY-openai note via `agency_sdk.auth_hooks`); tier sections removed (11913af)
- [x] Task: `examples/quick_gateway.py` — rewritten to `openai_client()`/`async_openai_client()` only (one-shot, streaming, async, wrong-`x-org` 403 via `openai.APIStatusError`) (11913af)
- [x] Task: `README.md` + `CLAUDE.md` + `conductor/tech-stack.md` synced (openai → core dep; `[openai]` extra + tier language removed) (11913af)
- [x] Task: Final gate run — pytest 121 passed, gateway modules 100%, total 92%; mypy/black/bandit clean; live E2E vs `:4000` 5/5 PASS (acce36a)
- [x] Task: Conductor - User Manual Verification 'Phase 3' — folded into the continuous run; live E2E is the verification (acce36a)
