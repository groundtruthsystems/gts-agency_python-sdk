# Session-reporting delegate — design (② gts-agency_python-sdk)

> Solidified 2026-07-17. Decision of record for a NEW, standalone SDK capability.
> **BUILT 2026-07-17** on the `feat/work-queue-delegate` branch (at the user's explicit instruction —
> it did not get its own branch; the "do not ride the files-inbox rc" constraint below is therefore
> now a RELEASE-time guardrail: do not publish this in the files-inbox rc12).
> Sibling to — and independent of — the `files_inbox_ingestion_20260713` track (that track PRODUCES
> the injected `session_id`; this delegate CONSUMES it).

## Build status (2026-07-17)

Shipped: `delegates/session_dto.py` (`SessionStatus`, `SessionCommandResponse` transcribed from the
live server `SessionCommandResponse`, `AnalyticsEvent` promoted verbatim), `delegates/session_client.py`
(`attach` + `update` only; **no `register`**), `.sessions()` facade accessor, and the base-client
connection-retry. 176 offline tests, mypy strict, black, bandit all clean; `update` wire shape
live-smoked against ①'s branch on `localhost:13001` (server 404s on a bogus session — envelope
accepted).

**Deviation from §4 (retry), forced by adversarial review:** a blanket "every delegate retries" is
UNSAFE — a `requests.ConnectionError` can be a reset that arrives after the server committed, so
auto-retrying a POST would double-send (e.g. re-hit the work-queue UNIQUE key and mis-report a won
claim as lost). Retry is therefore gated to pure reads (GET/HEAD/OPTIONS) by default; writers opt in
per call (`retry=True`). `update` opts in (idempotent + ①'s status-monotonicity guard). Read timeouts
and HTTP status codes are never retried.

**Signature deviation (informs §6 below):** `update`'s first positional is `organisation_id` (the CP
`SessionCommand` requires `organisation` in the body + `?o=` query, which §2's shown signature
omitted). Call it `sessions.update(organisation_id, status=…, session_id=…, events=…)`.

## 0. Decision (CTO 2026-07-17) + chosen option

An agent must **never self-register** its own control-plane session — in BOTH the direct-uri and
work-queue paths it **inherits** the dispatched session (the CloudEvent `event.id()`, injected into
the agent's `arguments.session_id` by the ① worker, committed `222d25e3`). Self-registration mints a
second session divorced from the work-item card (the "orphan leg ②") — the exact bug this policy
kills.

**Chosen path = Option B:** do NOT write a throwaway bespoke adopt in ③; instead build this delegate
in ② and have ③ adopt it. (③-adopt was never implemented, so B wastes nothing and fixes the
OAuth/plumbing duplication at the same moment it enforces the policy.)

## 1. The seam — what moves to ② vs stays in ③

- **MOVE to ② (transport / CP-contract):** the two session command shapes + the status int map + the
  OAuth/Bearer/org-scoping/retry plumbing that ③'s bespoke `guideline_agent/events/control_plane_client.py`
  re-implements (~95% duplicates the SDK). The `AnalyticsEvent` DTO (a cross-agent shared shape).
- **STAY in ③ (agent domain):** the publisher's in-process orchestration (batching / flush cadence /
  daemon `Timer` / `atexit` / classmethod singleton), the terminal payload reducers
  (`_terminal_error_from_response`, `_terminal_metrics_from_response`), the pipeline-status→session-status
  DECISION and the exit-code contract (`_terminal_exit_code`, `_NONZERO_TERMINAL_EXIT=4`), the local
  `EventManager` (events.json), and the log-capture policy. **The SDK never infers the outcome — the
  agent passes an already-decided status in.**

The publisher subsystem is deliberately left in ③ because it is process-global stateful, the opposite
of the SDK's stateless-per-call, instance-based delegates (`base_client.py` holds only
`base_url`+`token_supplier`). Only the single `update(..., events=…)` call inside `flush` is contract.

## 2. Public API — `AgencySessionClient(BaseDelegateClient)`

`api_path = "/api/sessions"`. A near-twin of `AgencySessionVaultClient` (`delegates/session_vault_client.py:48`,
same path space; the `.sessions()` accessor name is free). Reuses the SDK's shipped auth
(`credentials.py` OAuth2 client_credentials) + request plumbing (`base_client.py:22-51`) for free.

**Exposed methods (ONLY these two):**

```python
def attach(self, session_id: str) -> None:
    """Bind this client to an EXISTING dispatched session. The agent's only entry point —
    it inherits, it does not create. No HTTP call; just records the id as the update target."""

def update(
    self,
    session_id: str,
    *,
    status: SessionStatus | int,        # caller-decided; the client MARSHALS, never infers
    result: dict | None = None,
    events: list[dict] | None = None,
    metrics: dict | None = None,
    error: str | None = None,
    logs: str | None = None,
) -> dict:
    """POST /api/sessions/{session_id}/_command  {command:"update", update:{…}}.
    Status int map (from ③ control_plane_client.py:176,179): failed → -1;
    completed-with-result → 0; in-progress → 2."""
```

**`register` is NOT exposed.** The CP `register` command exists in the contract, but exposing it as a
first-class agent API is a footgun that reintroduces the orphan session. If a non-agent/migration
caller ever truly needs it, add it later as an explicitly `internal`/deprecated method with a
docstring warning that it violates the inherit policy — never on the normal path.

## 3. Shared DTO — promote `AnalyticsEvent` into the SDK

③'s `guideline_agent/events/event.py` (the fixed `%Y-%m-%d %H:%M:%S` serialization) is the cross-agent
"sibling-contract" event shape that every agent re-ports today. Move it to `agency_sdk` (e.g.
`delegates/session_dto.py` alongside the session command DTOs) so all agents emit one shape.

## 4. Base-client addition — fold in retry (the one genuine net-new win)

`base_client.py:30-51` (`_request`) is a bare `requests`/httpx call with no retry. ③ carries a 3-attempt
exponential backoff (`control_plane_client.py` `_with_retry`/`_RETRY_WAITS`, httpx-error-only). Fold
that into `BaseDelegateClient` so **every** delegate gains resilience — an addition, not a dedup.

## 5. Facade wiring (4-touch, identical to every delegate)

1. `delegates/session_dto.py` — DTOs mirroring the Rust `session` command shapes (`SessionUpdateResult`,
   a `SessionStatus` enum for the -1/0/2 map).
2. `delegates/session_client.py` — `AgencySessionClient(BaseDelegateClient)` per §2.
3. `client.py:22-36` — instantiate it in `AgencyClient.__init__` (mirror the `session_vault`/`work_queues`
   lazy-init at `client.py:57-64`).
4. `client.py` — add the `.sessions()` facade accessor.

## 6. What ③ does (contract-conformant wiring — this repo's part, done AFTER ② ships)

- DELETE the duplicated OAuth/request plumbing in `guideline_agent/events/control_plane_client.py`;
  keep only what the SDK doesn't own.
- Rewire `guideline_agent/agency.py:_configure_event_channels` (~:316): construct the SDK
  `AgencySessionClient`, read `input_data["arguments"].get("session_id")`; if present `.attach(id)`,
  else no CP client (no-op reporting). **Drop the unconditional `register_session`.**
- `ControlPlanePublisher.flush()`'s single CP call becomes
  `sessions.update(organisation_id, status=…, session_id=session_id, events=…)` (note the
  `organisation_id`-first signature — see the build-status deviation note above);
  everything else in the publisher (thread/atexit/batching/terminal reducers) stays.
- Declare `session_id` as an internal/platform-injected input in `AGENCY_CARD.yml`.
- Update `tests/test_agency_events_wiring.py` (inject `session_id` → assert adopt+update, no register).

**Safety (already in place on ①):** after ③ adopts leg ①, both the worker publisher and the agent
write to leg ①; ①'s session-status monotonicity guard `42affd8c` prevents terminal regression.

## 7. Sequencing / Gate

This is a NEW ② surface ⇒ a new rc ⇒ Gate B. Keep it OFF the files-inbox critical path:

1. ② build the delegate + DTO + `.sessions()` + retry-in-base (this design). Unit-test the shapes.
2. ③ adopt against the **editable** `../gts-agency_python-sdk` dep; prove the full
   inject → attach → update chain writes real session output on the local docker stack (Gate A).
3. Only then: ② publish the rc + ③ pin/merge (Gate B, explicit user go).

No ①-server work is required — the `register`/`update` CP commands are already live; ① already
injects `session_id` (`222d25e3`).

## 8. Constraints / anti-goals (design guardrails)

- **Only `attach` + `update` are agent-facing.** No `register` on the normal path.
- **No threaded/`atexit`/singleton publisher inside the SDK delegate layer.** If a reusable batcher is
  ever wanted, mirror the opt-in, instance-owned `observability/` subsystem — not a delegate.
- **The SDK marshals a caller-decided status; it never infers the run outcome.**
- **Independent of files-inbox** — its own future conductor track; do not bundle the rc.
