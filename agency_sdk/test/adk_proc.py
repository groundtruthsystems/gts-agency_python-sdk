"""Child-process entry points for the Agent Vault multi-process scenarios (run with ``python -m``).

Each command talks to a file-backed ``FakeControlPlane`` so separate processes share one fake comand.

- ``write <backend.json> <out.json>``: create a session, append a varied history (bytes, sets, floats,
  ``None`` fields, state deltas), and dump the in-memory session as written.
- ``read <backend.json> <session_id> <out.json>``: read the session back and dump it.
"""

from __future__ import annotations

import asyncio
import json
import sys

from google.adk.events import Event, EventActions
from google.genai import types

from agency_sdk.adk import GtsSessionService
from agency_sdk.test.adk_fakes import FakeControlPlane


def _service(path: str) -> GtsSessionService:
    cp = FakeControlPlane(path)
    return GtsSessionService(
        vault=cp.vault, sessions=cp.sessions, templates=cp.templates, organisation_id=2, template_name="adk-agent"
    )


def _dump(session) -> str:
    return json.dumps(
        {
            "id": session.id,
            "app_name": session.app_name,
            "user_id": session.user_id,
            "state": session.state,
            "events": [json.loads(e.model_dump_json(by_alias=True)) for e in session.events],
        },
        sort_keys=True,
    )


async def _write(path: str, out: str) -> None:
    svc = _service(path)
    session = await svc.create_session(app_name="app", user_id="user-1", state={"topic": "x", "user:lang": "en"})
    events = [
        Event(
            invocation_id="inv-1",
            author="user",
            timestamp=1_700_000_000.125,
            content=types.Content(
                role="user",
                parts=[
                    types.Part(text="look at this"),
                    types.Part(inline_data=types.Blob(mime_type="image/png", data=bytes(range(256)))),
                ],
            ),
        ),
        Event(
            invocation_id="inv-1",
            author="agent",
            timestamp=1_700_000_001.5,
            content=types.Content(
                role="model", parts=[types.Part(function_call=types.FunctionCall(name="ask", args={"q": 1}, id="c1"))]
            ),
            long_running_tool_ids={"c1", "c2"},
            actions=EventActions(state_delta={"step": 2, "app:flag": True, "temp:scratch": 9}),
        ),
    ]
    for event in events:
        await svc.append_event(session, event)
    session.state.pop("temp:scratch", None)  # temp: is in-memory only; the reader must not see it
    with open(out, "w") as fh:
        fh.write(_dump(session))


async def _read(path: str, session_id: str, out: str) -> None:
    session = await _service(path).get_session(app_name="app", user_id="user-1", session_id=session_id)
    with open(out, "w") as fh:
        fh.write(_dump(session))


def main(argv: list[str]) -> None:
    command, *args = argv
    if command == "write":
        asyncio.run(_write(*args))
    elif command == "read":
        asyncio.run(_read(*args))
    else:
        raise SystemExit(f"unknown command {command!r}")


if __name__ == "__main__":
    main(sys.argv[1:])
