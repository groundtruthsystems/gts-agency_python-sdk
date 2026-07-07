# Plan — Gateway streaming + openai SDK integration

Methodology: TDD per `workflow.md` (Red → Green → Refactor), >80% coverage,
mypy strict / black / bandit gates, Phase Completion Verification Protocol per phase.

## Phase 1: Native streaming + facade API fix [checkpoint: 69596dd]

- [x] Task: Facade API fix — `environment`/`gateway_base_url` mutual exclusion (TDD) (3f5297a)
    - [x] Failing tests: both given → `ValueError` (no network call); discovery with `environment=None` defaults to production; explicit URL alone and env-discovery alone still work
    - [x] Implement: `environment: str | None = None` + guard in `gateway()`; adjust docstring
- [x] Task: Write failing tests for native streaming (`test_gateway_streaming.py`) (31d8c2e)
    - [x] Chunk DTOs parse multi-chunk SSE shapes incl. usage-only final chunk and empty deltas
    - [x] `chat_completions_stream` posts `stream: true`, `requests(stream=True)`, parses `data:` lines, stops at `[DONE]`, closes the response (incl. early generator exit)
    - [x] `complete_stream` yields only non-empty content deltas
    - [x] Guard: `chat_completions` with truthy `stream` → `ValueError` before any HTTP call
    - [x] conftest: additive `StubResponse.iter_lines(delimiter=...)` + `close()` support
    - [x] Regression (found by live E2E): multibyte UTF-8 delta (`✅`, raw `\xe2\x9c\x85`) parses intact — text/event-stream has no charset → requests defaults ISO-8859-1 → 0x85 byte became U+0085 NEL → `splitlines()` cut the JSON mid-string; fixed with byte-mode `iter_lines(delimiter=b"\n")` + explicit per-line UTF-8 decode
- [x] Task: Implement chunk DTOs + `chat_completions_stream`/`complete_stream` + guard (Green) (31d8c2e)
- [x] Task: Refactor; verify coverage / mypy / black / bandit (31d8c2e)
- [x] Task: Conductor - User Manual Verification 'Phase 1: Native streaming + facade API fix' (Protocol in workflow.md) — incl. live `:4000` streaming E2E (69596dd)

## Phase 2: [openai] extra + full-feature helpers [checkpoint: c09d3ac]

- [x] Task: Write failing tests (`test_gateway_openai.py`, `pytest.importorskip("openai")` for functional ones) (76e2c84)
    - [x] Missing-extra guard raises a clear `ImportError` naming `[openai]`
    - [x] `openai_client()`/`async_openai_client()`: base_url `{gateway}/v1`, `x-org` default header, rotating-bearer auth hook on the http_client, kwargs passthrough (+ no-cache: caller owns lifecycle)
- [x] Task: Implement `pyproject` extra + helpers on `AgencyGatewayClient` (Green); install `.[openai]` into the dev venv (76e2c84)
- [x] Task: Refactor; verify coverage / mypy / black / bandit (76e2c84)
- [x] Task: Conductor - User Manual Verification 'Phase 2: [openai] extra + helpers' (Protocol in workflow.md) — incl. live helper E2E (complete + stream) (c09d3ac)

## Phase 3: Docs, example & gates

- [ ] Task: `docs/gateway.md` — tier model, openai section (helpers + recipe + rotation patterns), native streaming, URL-vs-env semantics; reposition built-in client
- [ ] Task: `README.md` + `CLAUDE.md` sync; `examples/quick_gateway.py` streaming step + optional openai step
- [ ] Task: Final gate run (pytest+cov, mypy, black, bandit)
- [ ] Task: Conductor - User Manual Verification 'Phase 3: Docs, example & gates' (Protocol in workflow.md)

## Phase 4: Adversarial review (ultracode)

- [ ] Task: Multi-agent review (correctness / API-design / security lenses) of the new streaming + openai code; fix confirmed findings
- [ ] Task: Conductor - User Manual Verification 'Phase 4: Adversarial review' (Protocol in workflow.md)
