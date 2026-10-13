# Durable Google ADK 2 sessions on the GTS session vault

`agency_sdk.adk` makes Google ADK 2 agents durable. It provides three pieces:

| Piece | What it gives you |
|---|---|
| `GtsSessionService` | An ADK `BaseSessionService` that stores every session in its GTS session vault. Sessions survive restarts and can move between workers. |
| `DurableRunner` | Runs an agent by **correlation id**. A run that is killed part-way resumes from its last checkpoint, and completed steps are not repeated. |
| HITL | A run can pause for a human. The human answers in comand as an authenticated user, and the run continues from where it stopped. |

Design and backend contract: [`agent_vault_gap_analysis.md`](agent_vault_gap_analysis.md). The comand
endpoints this relies on are being built in the backend track. Until they ship, the SDK is proven against
offline fakes of that contract (`agency_sdk/test/adk_fakes.py`).

## Install

```bash
pip install gts-agency-python-sdk[adk]   # google-adk>=2.11,<3, lazy-imported
```

`import agency_sdk` never loads ADK; only `import agency_sdk.adk` does.

## Switch from `InMemorySessionService` (configuration only)

```python
from agency_sdk.adk import GtsSessionService

session_service = GtsSessionService.from_client(client, organisation_id=2, template_name="adk-agent")
runner = Runner(app=app, session_service=session_service)   # was: InMemorySessionService()
```

Or, through ADK's service registry. This reads its credentials from `AGENCY_AUTH_URL`, `AGENCY_CLIENT_ID`
and `AGENCY_CLIENT_SECRET`:

```python
from agency_sdk.adk import register_gts_session_service

register_gts_session_service()
# adk web --session_service_uri "gts://comand.example.com?org=2&template=adk-agent"
```

Add `tls=0` to the URI for plain HTTP during local development.

**How ADK sessions map to GTS sessions.** One ADK session is one GTS session: the ADK `session_id` *is*
the GTS session id.
- A dispatched agent passes the session id it was given, and the service adopts that session.
- `create_session` without an id registers a new GTS session from `template_name`. The ADK `user_id` is
  sent as the session's owner claim.

## What is stored

| Key | Holds | Classification |
|---|---|---|
| `adk.session` | The head: identity, revision, event count, last event key, session state | restricted |
| `adk.ev.<seq8>.<event_id>` | One immutable record per event, linked to the previous one | restricted |
| `adk.failure` | A failure summary: exception **type** and ids only | restricted |
| `adk.state` with `scope=app` / `scope=user` | ADK `app:` / `user:` state | restricted (app may opt into `internal`) |

The rules that apply across these records:
- **`temp:` state** is never persisted.
- **Scoped state.** comand resolves `scope=user` from the session's bound owner and `scope=app` from its
  app, so the SDK never names a user. A session with no bound owner is refused (fail closed).
- **`get_user_state`** (with no session) raises `NotImplementedError`.
- **Printing records.** Record `repr`s never show state, `user_id` or event content. Do not log whole ADK
  sessions or events yourself; they carry PHI.
- **Retention.** The vault is kept after a session ends, for forensics. Retention and erasure are
  comand's policy.

## Checkpoints and concurrency

Every non-partial event that ADK appends is a **checkpoint**, committed before the runner moves on:

1. The event record is written only if its key is absent.
2. The head is written only if its version is unchanged. This is the only commit point.

A writer whose copy of the session is out of date gets ADK's `StaleSessionError` and has changed nothing
visible. An event left behind by a lost race is never linked from the head, so reads ignore it.
`get_session` honours `num_recent_events` and `after_timestamp`, and never writes.

## Durable runs by correlation id

```python
from agency_sdk.adk import DurableRunner

durable = DurableRunner(app=app, session_service=session_service, hitl=client.session_hitl())
outcome = await durable.run(correlation_id, user_id="user-1", message="Review case 42")
```

The app must be built with `App(..., resumability_config=ResumabilityConfig(is_resumable=True))`.

A correlation id names **one run**. Make it high-entropy and opaque, never a clinical identifier. What
`run` does depends on the run's current status:

| The run is | `run(...)` returns |
|---|---|
| new | creates the session, runs it, returns `completed` (or pauses) |
| in progress (for example the worker was killed) | resumes the unfinished invocation, returns `completed` |
| completed | `already_completed` with the stored final text; the model is not called |
| failed | `failed` with `error_type`. Start a new correlation id to retry, because FAILED is terminal in comand |
| racing another worker | one of them returns `conflict` and writes nothing further |

How failures are handled:
- An infrastructure error (vault or network) propagates and leaves the session `IN_PROGRESS`, so a later
  run resumes it.
- An agent error writes `adk.failure` (the exception type only, since messages can carry PHI) and then
  marks the session FAILED.

ADK resumes a tool call that was in flight when the process died **at least once**, so tools with side
effects must be idempotent. Steps whose results were committed are never repeated.

## Human input (HITL)

1. A tool that needs a human is a `LongRunningFunctionTool` whose function **returns `None`**. If it
   returns a value, ADK treats that value as the answer and does not pause.
2. When the run pauses, `DurableRunner` creates one comand request per pending call. The request is
   `confidential` and expires in 7 days by default (set `hitl_ttl` per runner or per run). comand sets
   the session to `AWAITING_INPUT`, and `run` returns `awaiting_input` with `pending_requests`.
3. A reviewer answers **in comand as themselves**: `client.session_hitl().respond(...)`, called by a
   client built with the human's own token. comand rejects service principals, keeps only the first
   answer, and records the verified `responder`.
4. A re-dispatched worker calls `durable.resume(correlation_id, user_id=...)`. `resume` takes no answer.
   It reads comand's recorded answers and injects them only when **every** request of the paused
   invocation is answered and the ids match. `outcome.answered_by` lists the responders.

| Situation | Result |
|---|---|
| A duplicate or concurrent resume | The answer is injected once; the committed `FunctionResponse` is the marker |
| The session or correlation id does not exist | `SessionNotFound` (`resume` never creates a run) |
| An answer is bound to another invocation | `RequestNotFound`, and nothing is written |
| The request expired (comand's background job, server time) | `HitlExpired`, and the session is FAILED |
| An answer is larger than `max_answer_bytes` (64 KiB) | `VaultLimitExceeded` |

Answers reach the agent only as tool results, never as instructions. Treat them as untrusted input.

## Errors

`agency_sdk.adk.errors` defines the typed errors:

| Error | Meaning |
|---|---|
| `CorruptRecord` / `UnsupportedSchema` | A stored record could not be decoded, or comes from a newer schema. The read fails closed |
| `VaultLimitExceeded` | Over 1 MiB per value or 10,000 events per session (configurable via `VaultLimits`), checked before any request |
| `InvalidState` | Session state holds a value that is not JSON-native |
| `VaultUnavailable` | A transport error or unexpected HTTP status; the message never includes the request URL |
| `SessionNotFound`, `RequestNotFound`, `HitlExpired` | As described in the HITL section |

Stale writes raise ADK's own `google.adk.errors.StaleSessionError`.

## Examples

- [`examples/quick_gts_session_service.py`](../examples/quick_gts_session_service.py): one process writes
  a session and a fresh service reads it back exactly.
- [`examples/quick_durable_hitl.py`](../examples/quick_durable_hitl.py): `run` (pause, then exit),
  `answer` (as a human), then `resume` (finish).
