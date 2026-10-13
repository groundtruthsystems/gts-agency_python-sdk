"""TS-46: the README ``GtsSessionService`` quick-start and both ADK examples run against the offline fakes."""

import asyncio
import importlib.util
import pathlib
import re
import textwrap

import pytest

pytest.importorskip("google.adk")

from agency_sdk.adk import GtsSessionService  # noqa: E402
from agency_sdk.test.adk_fakes import FakeControlPlane  # noqa: E402
from agency_sdk.test.adk_llm_fakes import CountingTools, make_app, steps_script  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]


class _FakeAgencyClient:
    """Just the facade accessors the quick-start uses, backed by the fake control plane."""

    def __init__(self, cp: FakeControlPlane) -> None:
        self._cp = cp
        self.base_url = "http://cp.test"

    def session_vault(self):
        return self._cp.vault

    def sessions(self):
        return self._cp.sessions

    def session_templates(self):
        return self._cp.templates

    def session_hitl(self):
        return self._cp.hitl


def _load_example(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "examples" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_readme_quickstart_runs(tmp_path):
    readme = (ROOT / "README.md").read_text()
    match = re.search(r"<!-- adk-quickstart -->\n```python\n(.*?)```", readme, re.S)
    assert match, "README quick-start block not found"
    namespace = {
        "client": _FakeAgencyClient(FakeControlPlane()),
        "app": make_app(steps_script(2), [CountingTools(tmp_path / "c.json").step_tool()]),
    }
    exec(
        "async def _quickstart():\n" + textwrap.indent(match.group(1), "    ") + "\n    return outcome", namespace
    )  # nosec B102 - our own README

    outcome = asyncio.run(namespace["_quickstart"]())

    assert outcome.status == "completed"


def test_session_service_example_runs():
    cp = FakeControlPlane()
    example = _load_example("quick_gts_session_service")

    def make_service() -> GtsSessionService:
        return GtsSessionService(
            vault=cp.vault, sessions=cp.sessions, templates=cp.templates, organisation_id=2, template_name="adk-agent"
        )

    asyncio.run(example.run_example(make_service))


def test_durable_hitl_example_runs_stage_by_stage():
    cp = FakeControlPlane()
    example = _load_example("quick_durable_hitl")
    outcomes = []
    for stage in ("run", "answer", "resume"):
        service = GtsSessionService(
            vault=cp.vault, sessions=cp.sessions, templates=cp.templates, organisation_id=2, template_name="adk-agent"
        )
        outcomes.append(
            asyncio.run(
                example.run_example(
                    stage, service=service, hitl=cp.hitl, human_hitl=cp.hitl, correlation_id="c-docs-01"
                )
            )
        )

    assert outcomes[-1].status == "completed" and outcomes[-1].answered_by == ["reviewer-1"]
