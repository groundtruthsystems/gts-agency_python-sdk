# Plan — Agent Gateway Client (SDK side)

Methodology: TDD per `workflow.md` (Red → Green → Refactor), >80% coverage,
mypy strict / black / bandit gates, and the Phase Completion Verification
Protocol at the end of every phase.

Design source: `docs/gateway_design.md` (§5 SDK design, §9 Phase 1 scope,
§10 live-validated contract). Release (rc11 bump + tag) is post-merge, out of track.

## Phase 1: Gateway DTOs [checkpoint: 5dfb002]

- [x] Task: Write failing tests for gateway DTOs (`agency_sdk/test/test_gateway_dto.py`) (da82696)
    - [x] `ChatMessage` role/content; content defaults to `None`
    - [x] `ChatCompletionRequest` accepts extra OpenAI params (`temperature`, `max_tokens`, ...) via `extra="allow"` and round-trips them through `model_dump`
    - [x] `ChatCompletionResponse`/`ChatChoice` parse the live-validated response shape (id/model/object/created/choices/usage/timings) and tolerate unknown fields
    - [x] Empty-content case (`content: null` + `reasoning_content`) parses; `message.content` is `None`
    - [x] Discovery DTOs `AgentGatewayStatusResponse`/`AgentGatewayEnvironmentResponse` parse a control-plane-shaped payload (both slots, missing slots, missing url)
- [x] Task: Implement `agency_sdk/delegates/gateway_dto.py` (Green) (da82696)
    - [x] Chat DTOs: `ChatMessage`, `ChatCompletionRequest`, `ChatChoice`, `ChatCompletionResponse` — Pydantic v2, snake_case, `extra="allow"`
    - [x] Discovery DTOs: `AgentGatewayEnvironmentResponse`, `AgentGatewayStatusResponse` — `extra="allow"`, modeled from `agent_gateway_dto.rs:24-47` (verification-deferred)
- [x] Task: Refactor; verify coverage / mypy / black / bandit (da82696)
- [x] Task: Conductor - User Manual Verification 'Phase 1: Gateway DTOs' (Protocol in workflow.md) (5dfb002)

## Phase 2: AgencyGatewayClient [checkpoint: abc2613]

- [x] Task: Write failing tests for `AgencyGatewayClient` (`agency_sdk/test/test_gateway_client.py`) (c5692f9)
    - [x] `chat_completions` POSTs to `{gateway_base_url}/v1/chat/completions` (host is the gateway, trailing `/` normalized) with `timeout=120`
    - [x] Headers carry `Authorization: Bearer <token>`, `Content-Type: application/json`, and `x-org: <org_id>` (exact lowercase name)
    - [x] Request body is `model_dump(mode="json", by_alias=True, exclude_none=True)` — extra params included, `None` content excluded
    - [x] `complete(messages, model, **kw)` returns `choices[0].message.content`; empty/None content returns `""`
    - [x] HTTP 401/403 with plain-text bodies propagate via `raise_for_status` (no JSON assumption)
- [x] Task: Implement `agency_sdk/delegates/gateway_client.py` (Green) (c5692f9)
    - [x] Sibling of `BaseDelegateClient` (own host/path/timeout/headers), sync `requests`, per design §5.5
- [x] Task: Refactor; verify coverage / mypy / black / bandit (c5692f9)
- [x] Task: Conductor - User Manual Verification 'Phase 2: AgencyGatewayClient' (Protocol in workflow.md) (abc2613)

## Phase 3: Facade accessor + URL discovery

- [x] Task: Write failing tests for `AgencyClient.gateway(...)` and `_discover_gateway_url` (`agency_sdk/test/test_gateway_facade.py`) (ecea91d)
    - [x] `gateway(org_id=..., gateway_base_url=...)` returns an `AgencyGatewayClient` bound to the shared `token_supplier`; repeated calls return the same instance (DCL cache)
    - [x] Concurrent calls construct exactly one instance (thread-safety, mirrors observability H1 test)
    - [x] With `gateway_base_url` omitted: one `GET {base_url}/api/agentgateways?o={org}` with the control-plane bearer; `environment="production"` → `production.url`, `environment="test"` → `test.url`
    - [x] Missing slot / missing url / empty list raises a clear `ValueError` (+ unknown environment; + page-wrapped payload tolerance)
- [x] Task: Implement facade wiring in `agency_sdk/client.py` (Green) (ecea91d)
    - [x] `_gateway` + `_gateway_lock` fields, `gateway(*, org_id, gateway_base_url=None, environment="production")`, lazy import, DCL
    - [x] `_discover_gateway_url(org_id, environment)` (verification-deferred: offline-tested, no local live check)
- [x] Task: Refactor; verify coverage / mypy / black / bandit (ecea91d)
- [ ] Task: Conductor - User Manual Verification 'Phase 3: Facade accessor + URL discovery' (Protocol in workflow.md)

## Phase 4: Example, docs & quality gates

- [ ] Task: Add `examples/quick_gateway.py` (env-driven, self-verifying, exit non-zero on failure)
    - [ ] Reads `AGENCY_AUTH_URL`, `AGENCY_CLIENT_ID`, `AGENCY_CLIENT_SECRET`, `AGENCY_ORG_ID`, `GATEWAY_BASE_URL`, `GATEWAY_MODEL`
    - [ ] Calls `client.gateway(...).complete(...)`; asserts non-empty text; prints PASS per step
- [ ] Task: Documentation
    - [ ] `docs/gateway.md` — usage guide (accessor, prod/test URLs, `x-org`, discovery fallback, local E2E)
    - [ ] Update `README.md` "Delegate Clients" + `CLAUDE.md` Architecture with the gateway delegate
- [ ] Task: Final gate run
    - [ ] `pytest --cov=agency_sdk --cov-report=term-missing` (>80%), `mypy agency_sdk/`, `black --check agency_sdk/ examples/`, `bandit -r agency_sdk/ -x agency_sdk/test`
- [ ] Task: Conductor - User Manual Verification 'Phase 4: Example, docs & quality gates' (Protocol in workflow.md)
