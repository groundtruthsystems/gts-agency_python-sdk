# Plan — Gateway streaming + openai SDK integration

Methodology: TDD per `workflow.md` (Red → Green → Refactor), >80% coverage,
mypy strict / black / bandit gates, Phase Completion Verification Protocol per phase.

## Phase 1: Native streaming + facade API fix

- [ ] Task: Facade API fix — `environment`/`gateway_base_url` mutual exclusion (TDD)
    - [ ] Failing tests: both given → `ValueError` (no network call); discovery with `environment=None` defaults to production; explicit URL alone and env-discovery alone still work
    - [ ] Implement: `environment: str | None = None` + guard in `gateway()`; adjust docstring
- [ ] Task: Write failing tests for native streaming (`test_gateway_streaming.py`)
    - [ ] Chunk DTOs parse multi-chunk SSE shapes incl. usage-only final chunk and empty deltas
    - [ ] `chat_completions_stream` posts `stream: true`, `requests(stream=True)`, parses `data:` lines, stops at `[DONE]`, closes the response (incl. early generator exit)
    - [ ] `complete_stream` yields only non-empty content deltas
    - [ ] Guard: `chat_completions` with truthy `stream` → `ValueError` before any HTTP call
    - [ ] conftest: additive `StubResponse.iter_lines()` support
- [ ] Task: Implement chunk DTOs + `chat_completions_stream`/`complete_stream` + guard (Green)
- [ ] Task: Refactor; verify coverage / mypy / black / bandit
- [ ] Task: Conductor - User Manual Verification 'Phase 1: Native streaming + facade API fix' (Protocol in workflow.md) — incl. live `:4000` streaming E2E

## Phase 2: [openai] extra + full-feature helpers

- [ ] Task: Write failing tests (`test_gateway_openai.py`, `pytest.importorskip("openai")` for functional ones)
    - [ ] Missing-extra guard raises a clear `ImportError` naming `[openai]`
    - [ ] `openai_client()`/`async_openai_client()`: base_url `{gateway}/v1`, `x-org` default header, rotating-bearer auth hook on the http_client, kwargs passthrough
- [ ] Task: Implement `pyproject` extra + helpers on `AgencyGatewayClient` (Green); install `.[openai]` into the dev venv
- [ ] Task: Refactor; verify coverage / mypy / black / bandit
- [ ] Task: Conductor - User Manual Verification 'Phase 2: [openai] extra + helpers' (Protocol in workflow.md) — incl. live helper E2E (complete + stream)

## Phase 3: Docs, example & gates

- [ ] Task: `docs/gateway.md` — tier model, openai section (helpers + recipe + rotation patterns), native streaming, URL-vs-env semantics; reposition built-in client
- [ ] Task: `README.md` + `CLAUDE.md` sync; `examples/quick_gateway.py` streaming step + optional openai step
- [ ] Task: Final gate run (pytest+cov, mypy, black, bandit)
- [ ] Task: Conductor - User Manual Verification 'Phase 3: Docs, example & gates' (Protocol in workflow.md)

## Phase 4: Adversarial review (ultracode)

- [ ] Task: Multi-agent review (correctness / API-design / security lenses) of the new streaming + openai code; fix confirmed findings
- [ ] Task: Conductor - User Manual Verification 'Phase 4: Adversarial review' (Protocol in workflow.md)
