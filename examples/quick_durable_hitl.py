#!/usr/bin/env python3
"""Durable run with human input: pause, exit, a human answers later, a new process finishes the run.

Run the three stages as separate processes, sharing one correlation id:

    export CORRELATION_ID=$(python -c "import uuid; print(uuid.uuid4().hex)")
    python examples/quick_durable_hitl.py run      # the agent asks for approval, pauses, exits
    python examples/quick_durable_hitl.py answer   # a reviewer answers in comand, as themselves
    python examples/quick_durable_hitl.py resume   # a re-dispatched worker finishes the run

or all three in sequence with ``python examples/quick_durable_hitl.py all``.

- ``run`` and ``resume`` use the agent's client credentials (``AGENCY_*``).
- ``answer`` uses a **human's own** access token (``AGENCY_HUMAN_TOKEN``). comand refuses answers from
  service principals, keeps only the first answer and records the verified responder; the agent never
  sends an answer itself.
- The approval tool is a ``LongRunningFunctionTool`` whose function returns ``None``. That is what makes
  ADK pause; a tool that returns a value never pauses.

The agent uses a deterministic local model, so no LLM endpoint is needed. Prerequisites: the ``[adk]``
extra and a control plane with the Agent Vault endpoints (``docs/agent_vault_gap_analysis.md`` §4).
"""

import asyncio
import os
import sys
import traceback
import uuid
from typing import Any, AsyncGenerator

from google.adk.agents import LlmAgent
from google.adk.apps import App, ResumabilityConfig
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.tools.long_running_tool import LongRunningFunctionTool
from google.genai import types

from agency_sdk.adk import DurableRunner, GtsSessionService
from agency_sdk.client import AgencyClient, CredentialsSupplier
from agency_sdk.delegates.session_hitl_client import AgencySessionHitlClient

USER = "user-1"


def request_approval(question: str) -> None:
    """Ask a human reviewer to approve; the answer arrives later through comand."""
    return None  # returning None is what pauses the run


class ApprovalModel(BaseLlm):
    """A deterministic stand-in for an LLM: ask for approval, then report the reviewer's answer."""

    model: str = "approval"

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        answers = [p.function_response for c in llm_request.contents for p in c.parts or [] if p.function_response]
        if not answers:
            call = types.FunctionCall(
                name="request_approval", args={"question": "Publish the summary?"}, id="approval-1"
            )
            part = types.Part(function_call=call)
        else:
            part = types.Part(text=f"Reviewer said: {answers[-1].response}")
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


def build_app() -> App:
    agent = LlmAgent(
        name="reviewer_flow", model=ApprovalModel(), tools=[LongRunningFunctionTool(func=request_approval)]
    )
    return App(name="hitl_demo", root_agent=agent, resumability_config=ResumabilityConfig(is_resumable=True))


async def run_example(
    mode: str,
    *,
    service: GtsSessionService,
    hitl: Any,
    human_hitl: Any,
    correlation_id: str,
) -> Any:
    """One stage of the flow. ``hitl`` is the agent's HITL client; ``human_hitl`` answers as a human."""
    runner = DurableRunner(app=build_app(), session_service=service, hitl=hitl)
    if mode == "run":
        outcome = await runner.run(correlation_id, user_id=USER, message="Prepare the summary")
        assert outcome.status == "awaiting_input", outcome
        print(f"run: paused for human input, pending requests {outcome.pending_requests}; exiting")
        return outcome
    if mode == "answer":
        ref = service.sessions.find_by_correlation_id(service.organisation_id, correlation_id)
        assert ref is not None, "no run for this correlation id"
        answered = human_hitl.respond(service.organisation_id, ref.session_id, "approval-1", {"approved": True})
        print(f"answer: recorded by comand (status {answered.status})")
        return answered
    if mode == "resume":
        outcome = await runner.resume(correlation_id, user_id=USER)
        assert outcome.status == "completed", outcome
        print(f"resume: finished -> {outcome.final_text!r} (answered by {outcome.answered_by})")
        return outcome
    raise ValueError(f"unknown mode {mode!r}")


class _StaticToken:
    """A token supplier for a human's own access token (for example from an OIDC login)."""

    def __init__(self, token: str) -> None:
        self._token = token

    def bearer_token(self) -> str:
        return self._token


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else "all"
    base_url = os.getenv("AGENCY_API_URL", "http://localhost:13001")
    credentials = CredentialsSupplier(
        auth_base_url=os.getenv("AGENCY_AUTH_URL", "http://localhost:8080/realms/agency/protocol/openid-connect/token"),
        client_id=os.getenv("AGENCY_CLIENT_ID", "your-client-id"),
        client_secret=os.getenv("AGENCY_CLIENT_SECRET", "your-client-secret"),
    )
    client = AgencyClient(token_supplier=credentials, base_url=base_url)
    organisation_id = int(os.getenv("AGENCY_ORG_ID", "2"))
    template = os.getenv("SESSION_TEMPLATE_NAME", "adk-agent")
    correlation_id = os.getenv("CORRELATION_ID") or uuid.uuid4().hex
    human = AgencySessionHitlClient(token_supplier=_StaticToken(os.getenv("AGENCY_HUMAN_TOKEN", "")), base_url=base_url)

    try:
        for stage in ("run", "answer", "resume") if mode == "all" else (mode,):
            # A fresh service per stage: nothing carries over in memory, as between processes.
            service = GtsSessionService.from_client(client, organisation_id=organisation_id, template_name=template)
            asyncio.run(
                run_example(
                    stage, service=service, hitl=client.session_hitl(), human_hitl=human, correlation_id=correlation_id
                )
            )
    except Exception:
        traceback.print_exc()
        print("FAILED")
        return 1
    print("ALL STEPS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
