# Plan — Gateway: openai-SDK-only (remove the zero-dep fallback)

Methodology: per `workflow.md` (tests drive the change), >80% coverage on retained code,
mypy strict / black / bandit gates, Phase Completion Verification Protocol per phase.
Removal-heavy: each phase's "test" step asserts the removed surface is gone AND the retained
surface still works, rather than red-before-green on new behavior.

## Phase 1: openai as a core dependency

- [ ] Task: Adjust tests + packaging for openai-as-core
    - [ ] Remove the missing-`[openai]`-extra guard test in `test_gateway_openai.py`
    - [ ] Add/keep a test asserting `openai` imports as a core dep (no extra needed) and the helpers still build
    - [ ] `pyproject.toml`: move `openai>=1.0.0` to `[project.dependencies]`; drop the `[openai]` extra
    - [ ] Remove `_require_openai()` + its calls from `gateway_client.py`; helpers import `openai` directly
- [ ] Task: Verify — full suite green; mypy / black / bandit; confirm dev install works without `[openai]`
- [ ] Task: Conductor - User Manual Verification 'Phase 1: openai as a core dependency' (Protocol in workflow.md)

## Phase 2: Strip the zero-dep surface

- [ ] Task: Remove tier-A methods from `gateway_client.py`
    - [ ] Delete `chat_completions`, `complete`, `chat_completions_stream`, `complete_stream`, `_headers`, dead `api_path`
    - [ ] Keep `__init__`, `openai_client`, `async_openai_client`, `_httpx_bearer_auth`
    - [ ] Delete `test_gateway_client.py` and `test_gateway_streaming.py`
- [ ] Task: Trim `gateway_dto.py` to discovery-only
    - [ ] Delete the 7 chat/chunk DTOs; keep `AgentGatewayEnvironmentResponse` / `AgentGatewayStatusResponse`
    - [ ] Prune `test_gateway_dto.py` to the discovery-DTO tests; update imports
    - [ ] Update the module docstring to discovery-only scope
- [ ] Task: Verify — facade/openai/discovery tests green; grep clean for removed symbols; mypy / black / bandit
- [ ] Task: Conductor - User Manual Verification 'Phase 2: Strip the zero-dep surface' (Protocol in workflow.md)

## Phase 3: Docs, example & gates

- [ ] Task: `docs/gateway.md` — collapse to the single openai path (+ short DIY note via `agency_sdk.auth_hooks`); remove tier-A sections
- [ ] Task: `examples/quick_gateway.py` — rewrite to `openai_client()`/`async_openai_client()` only (one-shot, streaming, wrong-`x-org` 403 via `openai.APIStatusError`)
- [ ] Task: `README.md` + `CLAUDE.md` + `conductor/tech-stack.md` sync (drop `[openai]` extra, tier language)
- [ ] Task: Final gate run (pytest+cov, mypy, black, bandit) + live E2E vs `:4000` (openai one-shot/stream/async + 403)
- [ ] Task: Conductor - User Manual Verification 'Phase 3: Docs, example & gates' (Protocol in workflow.md)
