"""A deterministic ADK model and agents for the Agent Vault tests — no network model is ever called.

``ScriptedLlm`` answers turn *n* with ``script[n]``, where *n* is the number of function responses
already in the request history. A tool-driven script therefore advances one step per tool round-trip
and replays identically on resume, which is what lets a killed run be compared with an uninterrupted one.

``CountingTools`` records every real tool execution in a JSON file outside the vault, so a test can
prove that a resumed run did not repeat a completed step (the file survives the process being killed).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, AsyncGenerator, Callable

from google.adk.agents import LlmAgent
from google.adk.apps import App, ResumabilityConfig
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.tools.long_running_tool import LongRunningFunctionTool
from google.genai import types


def call(name: str, call_id: str, **args: Any) -> types.Part:
    return types.Part(function_call=types.FunctionCall(name=name, args=args, id=call_id))


def text(value: str) -> types.Part:
    return types.Part(text=value)


class ScriptedLlm(BaseLlm):
    model: str = "scripted"
    script: list[Any] = []

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        responses = [p.function_response for c in llm_request.contents for p in (c.parts or []) if p.function_response]
        step = self.script[min(len(responses), len(self.script) - 1)]
        parts = step(responses) if callable(step) else step
        parts = parts if isinstance(parts, list) else [parts]
        yield LlmResponse(content=types.Content(role="model", parts=parts))


class CountingTools:
    """Tools whose executions are counted in a file (survives SIGKILL)."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def _bump(self, name: str) -> None:
        counts = json.loads(self.path.read_text()) if self.path.exists() else {}
        counts[name] = counts.get(name, 0) + 1
        self.path.write_text(json.dumps(counts))

    def counts(self) -> dict[str, int]:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def step_tool(self, on_step: Callable[[int], None] | None = None) -> Callable[..., dict[str, Any]]:
        def step(n: int) -> dict[str, Any]:
            """Perform step n."""
            self._bump(f"step-{n}")
            if on_step is not None:
                on_step(n)
            return {"done": n}

        return step

    def ask_human(self) -> LongRunningFunctionTool:
        def ask_human(question: str) -> None:
            """Ask a human; the answer arrives later (returns None so the run pauses)."""
            self._bump("ask_human")
            return None

        return LongRunningFunctionTool(func=ask_human)


def make_app(
    script: list[Any], tools: list[Any] | None = None, *, name: str = "app", state_output: str | None = None
) -> App:
    agent = LlmAgent(name="agent", model=ScriptedLlm(script=script), tools=tools or [], output_key=state_output)
    return App(name=name, root_agent=agent, resumability_config=ResumabilityConfig(is_resumable=True))


def steps_script(n_steps: int, final: str = "done") -> list[Any]:
    """Call ``step(1)``..``step(n)`` one per turn, then answer ``final`` with the last tool result."""
    script: list[Any] = [call("step", f"c{i}", n=i) for i in range(1, n_steps + 1)]
    script.append(lambda responses: text(f"{final}: {responses[-1].response}"))
    return script
