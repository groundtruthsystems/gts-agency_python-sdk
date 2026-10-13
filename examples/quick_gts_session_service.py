#!/usr/bin/env python3
"""GtsSessionService quick-start: an ADK 2 session written by one service is read back exactly by another.

Swapping ``InMemorySessionService`` for ``GtsSessionService`` is the only change an agent needs:

  1. Build ``GtsSessionService.from_client(...)`` (a new GTS session is registered from a template).
  2. Run a tiny agent through a standard ADK ``Runner`` — every event is checkpointed to the vault.
  3. Build a *fresh* service (as another process or worker would) and read the session back.
  4. Assert the events and state are identical, including ``user:`` state held in the user scope.

The agent uses a deterministic local model, so no LLM endpoint is needed.

Prerequisites: the ``[adk]`` extra, a running control plane with the Agent Vault endpoints
(``docs/agent_vault_gap_analysis.md`` §4), a session template named ``SESSION_TEMPLATE_NAME``
(default ``adk-agent``), and the usual ``AGENCY_*`` variables.
"""

import asyncio
import json
import os
import sys
import traceback
from typing import Any, AsyncGenerator, Callable

from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.genai import types

from agency_sdk.adk import GtsSessionService
from agency_sdk.client import AgencyClient, CredentialsSupplier

APP = "quickstart"
USER = "user-1"


class EchoModel(BaseLlm):
    """A deterministic stand-in for an LLM: answers with the last user text, upper-cased."""

    model: str = "echo"

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        texts = [p.text for c in llm_request.contents if c.role == "user" for p in c.parts or [] if p.text]
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=(texts[-1] if texts else "").upper())])
        )


def build_agent() -> LlmAgent:
    return LlmAgent(name="echo", model=EchoModel(), output_key="last_answer")


def _dump(session: Any) -> str:
    return json.dumps(
        {"state": session.state, "events": [json.loads(e.model_dump_json(by_alias=True)) for e in session.events]},
        sort_keys=True,
    )


async def run_example(make_service: Callable[[], GtsSessionService]) -> None:
    writer = make_service()
    session = await writer.create_session(app_name=APP, user_id=USER, state={"topic": "demo", "user:lang": "en"})
    print(f"1. created session {session.id}")

    runner = Runner(app_name=APP, agent=build_agent(), session_service=writer)
    message = types.Content(role="user", parts=[types.Part(text="hello vault")])
    async for _ in runner.run_async(user_id=USER, session_id=session.id, new_message=message):
        pass
    written = await writer.get_session(app_name=APP, user_id=USER, session_id=session.id)
    assert written is not None
    print(f"2. ran the agent: {len(written.events)} events checkpointed, state={sorted(written.state)}")

    reader = make_service()  # a fresh service: nothing shared in memory with the writer
    back = await reader.get_session(app_name=APP, user_id=USER, session_id=session.id)
    assert back is not None, "session not found by the second service"
    assert _dump(back) == _dump(written), "read-back differs from what was written"
    assert back.state["last_answer"] == "HELLO VAULT" and back.state["user:lang"] == "en"
    print("3. a fresh service read the session back exactly")


def main() -> int:
    credentials = CredentialsSupplier(
        auth_base_url=os.getenv("AGENCY_AUTH_URL", "http://localhost:8080/realms/agency/protocol/openid-connect/token"),
        client_id=os.getenv("AGENCY_CLIENT_ID", "your-client-id"),
        client_secret=os.getenv("AGENCY_CLIENT_SECRET", "your-client-secret"),
    )
    client = AgencyClient(token_supplier=credentials, base_url=os.getenv("AGENCY_API_URL", "http://localhost:13001"))
    organisation_id = int(os.getenv("AGENCY_ORG_ID", "2"))
    template = os.getenv("SESSION_TEMPLATE_NAME", "adk-agent")

    def make_service() -> GtsSessionService:
        return GtsSessionService.from_client(client, organisation_id=organisation_id, template_name=template)

    try:
        asyncio.run(run_example(make_service))
    except Exception:
        traceback.print_exc()
        print("FAILED")
        return 1
    print("ALL STEPS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
