"""Offline tests for ``DurableRunner`` — durable execution by correlation id (Phase 4).

Scenarios (``test-plan.md``): TS-16 status dispatch, TS-17 racing runners, TS-18 duplicate register, TS-19
SIGKILL resume without repeats, TS-21 transient outage, TS-48/49 completed runs, TS-50 failure record,
TS-51 nothing deleted, TS-52 resume-input selection, TS-69 failure/FAILED crash window, TS-71 tombstone.
"""

import asyncio
import json
import os
import subprocess
import sys
import threading

import pytest

pytest.importorskip("google.adk")

from google.adk.events import Event  # noqa: E402
from google.genai import types  # noqa: E402

from agency_sdk.adk import DurableRunner, GtsSessionService, errors  # noqa: E402
from agency_sdk.adk._records import FAILURE_KEY, HEAD_KEY  # noqa: E402
from agency_sdk.adk.durable import choose_resume_input  # noqa: E402
from agency_sdk.delegates.session_dto import SessionStatus  # noqa: E402
from agency_sdk.test.adk_fakes import FakeControlPlane, http_error  # noqa: E402
from agency_sdk.test.adk_llm_fakes import CountingTools, call, llm_calls, make_app, steps_script, text  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CID = "corr-0123456789abcdef"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def cp():
    return FakeControlPlane()


def _service(cp) -> GtsSessionService:
    return GtsSessionService(
        vault=cp.vault, sessions=cp.sessions, templates=cp.templates, organisation_id=2, template_name="adk-agent"
    )


def _runner(cp, tmp_path, script=None, tools=None, name="counts.json"):
    counting = CountingTools(tmp_path / name)
    app = make_app(script or steps_script(3), tools if tools is not None else [counting.step_tool()])
    return DurableRunner(app=app, session_service=_service(cp)), app, counting


def _statuses(cp):
    return [e["status"] for op, e in cp.log if op == "update"]


class TestRun:
    def test_unknown_correlation_id_creates_runs_and_completes(self, cp, tmp_path):
        """TS-16 (unknown)."""
        runner, app, tools = _runner(cp, tmp_path)

        outcome = run(runner.run(CID, user_id="user-1", message="go"))

        assert outcome.status == "completed"
        assert outcome.final_text == "done: {'done': 3}"
        assert tools.counts() == {"step-1": 1, "step-2": 1, "step-3": 1}
        session = cp.session(outcome.session_id)
        assert session["labels"]["correlation_id"] == CID and session["status"] == 0
        assert _statuses(cp)[0] == SessionStatus.IN_PROGRESS and _statuses(cp)[-1] == SessionStatus.COMPLETED

    def test_runner_requires_a_resumable_app(self, cp):
        with pytest.raises(ValueError):
            DurableRunner(app=make_app(steps_script(1), resumable=False), session_service=_service(cp))

    def test_awaiting_and_failed_sessions_do_not_run(self, cp, tmp_path):
        """TS-16 (awaiting / failed): no LLM call, no vault write."""
        runner, app, _ = _runner(cp, tmp_path)
        waiting = run(runner.session_service.create_session(app_name="app", user_id="user-1", correlation_id="c-wait"))
        failed = run(runner.session_service.create_session(app_name="app", user_id="user-1", correlation_id="c-fail"))
        with cp.shared.io():
            cp.shared.sessions[waiting.id]["status"] = 3
            cp.shared.sessions[failed.id]["status"] = -1
        cp.vault.log.clear()

        outcomes = (
            run(runner.run("c-wait", user_id="user-1", message="go")),
            run(runner.run("c-fail", user_id="user-1", message="go")),
        )

        assert [o.status for o in outcomes] == ["awaiting_input", "failed"]
        assert llm_calls(app) == [] and cp.vault.writes() == []

    def test_in_progress_session_is_hydrated_and_resumed(self, cp, tmp_path):
        """TS-16 (in progress) / UC-07b in-process: a run abandoned mid-way resumes its invocation."""
        runner, app, tools = _runner(cp, tmp_path, script=steps_script(3))

        async def abandon_after_two_steps():
            gen = runner.runner.run_async(user_id="user-1", session_id=await _created(runner), new_message=_msg("go"))
            responses = 0
            async for event in gen:
                if event.content and any(p.function_response for p in event.content.parts):
                    responses += 1
                    if responses == 2:
                        break
            await gen.aclose()

        run(abandon_after_two_steps())
        assert tools.counts() == {"step-1": 1, "step-2": 1}

        outcome = run(runner.run(CID, user_id="user-1", message="ignored on resume"))

        assert outcome.status == "completed"
        assert tools.counts() == {"step-1": 1, "step-2": 1, "step-3": 1}


async def _created(runner) -> str:
    session = await runner.session_service.create_session(app_name="app", user_id="user-1", correlation_id=CID)
    return session.id


def _msg(value: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part(text=value)])


class TestCompletedAndFailed:
    def test_completed_run_returns_stored_result_without_calling_the_llm(self, cp, tmp_path):
        """TS-48."""
        first, _, _ = _runner(cp, tmp_path)
        done = run(first.run(CID, user_id="user-1", message="go"))
        second, app, tools = _runner(cp, tmp_path, name="second.json")
        cp.vault.log.clear()

        again = run(second.run(CID, user_id="user-1", message="something else"))

        assert (again.status, again.final_text, again.session_id) == (
            "already_completed",
            done.final_text,
            done.session_id,
        )
        assert llm_calls(app) == [] and tools.counts() == {} and cp.vault.writes() == []

    def test_final_event_committed_without_status_is_completed_without_the_llm(self, cp, tmp_path):
        """TS-49."""
        first, _, _ = _runner(cp, tmp_path)
        done = run(first.run(CID, user_id="user-1", message="go"))
        with cp.shared.io():
            cp.shared.sessions[done.session_id]["status"] = 2  # killed before COMPLETED was posted
        second, app, _ = _runner(cp, tmp_path, name="second.json")

        again = run(second.run(CID, user_id="user-1", message="go"))

        assert again.status == "completed" and again.final_text == done.final_text
        assert llm_calls(app) == [] and cp.session(done.session_id)["status"] == 0

    def test_agent_error_writes_a_type_only_failure_record_first(self, cp, tmp_path):
        """TS-50: the PHI-bearing exception message never reaches the record or the status update."""

        def boom(n: int) -> dict:
            """Fail."""
            raise ValueError("patient PHI-SENTINEL-0001 not found")

        runner, _, _ = _runner(cp, tmp_path, script=[call("boom", "c1", n=1), text("x")], tools=[boom])
        order: list[str] = []
        original_set = cp.vault.set

        def spy(*args, **kwargs):
            if args[2] == FAILURE_KEY:
                order.append("failure")
            return original_set(*args, **kwargs)

        cp.vault.set = spy
        original_update = cp.sessions.update

        def spy_update(*args, **kwargs):
            if kwargs.get("status") == SessionStatus.FAILED:
                order.append("FAILED")
            return original_update(*args, **kwargs)

        cp.sessions.update = spy_update

        outcome = run(runner.run(CID, user_id="user-1", message="go"))

        assert (outcome.status, outcome.error_type) == ("failed", "ValueError")
        assert order == ["failure", "FAILED"]
        record = cp.vault.value(outcome.session_id, FAILURE_KEY)
        assert record["error_type"] == "ValueError"
        everything = json.dumps(record) + json.dumps(cp.log) + outcome.model_dump_json()
        assert "PHI-SENTINEL-0001" not in everything
        assert cp.vault.value(outcome.session_id, HEAD_KEY)["event_count"] >= 1  # history kept

    def test_failure_crash_window_and_rerun_after_failed(self, cp, tmp_path):
        """TS-69."""

        def boom(n: int) -> dict:
            """Fail."""
            raise KeyError("x")

        runner, _, _ = _runner(cp, tmp_path, script=[call("boom", "c1", n=1), text("x")], tools=[boom])
        original_update = cp.sessions.update

        def die_before_failed(*args, **kwargs):
            if kwargs.get("status") == SessionStatus.FAILED:
                raise SystemExit("killed between the failure record and FAILED")
            return original_update(*args, **kwargs)

        cp.sessions.update = die_before_failed
        with pytest.raises(SystemExit):
            run(runner.run(CID, user_id="user-1", message="go"))
        cp.sessions.update = original_update
        sid = cp.sessions.find_by_correlation_id(2, CID).session_id
        record_before = cp.vault.value(sid, FAILURE_KEY)

        rerun, app, _ = _runner(cp, tmp_path, script=[text("never")], tools=[], name="r.json")
        outcome = run(rerun.run(CID, user_id="user-1", message="go"))
        again = run(rerun.run(CID, user_id="user-1", message="go"))

        assert (outcome.status, outcome.error_type, again.status) == ("failed", "KeyError", "failed")
        assert cp.session(sid)["status"] == -1 and cp.vault.value(sid, FAILURE_KEY) == record_before
        assert llm_calls(app) == []

    def test_terminal_runs_delete_nothing(self, cp, tmp_path):
        """TS-51."""
        runner, _, _ = _runner(cp, tmp_path, script=[call("step", "c1", n=1), text("ok")])
        state_app = make_app([text("ok")], [], state_output=None)
        done = run(runner.run(CID, user_id="user-1", message="go"))
        cp.vault.set(2, done.session_id, "adk.state", {"state": {"pref": 1}}, scope="user")
        before = set(cp.vault.data)

        run(DurableRunner(app=state_app, session_service=_service(cp)).run(CID, user_id="user-1", message="x"))

        assert set(cp.vault.data) == before

    def test_tombstoned_session_is_not_found(self, cp, tmp_path):
        """TS-71."""
        runner, _, _ = _runner(cp, tmp_path)
        sid = run(_created(runner))
        run(runner.session_service.delete_session(app_name="app", user_id="user-1", session_id=sid))
        cp.vault.log.clear()

        with pytest.raises(errors.SessionNotFound):
            run(runner.run(CID, user_id="user-1", message="go"))

        assert cp.vault.writes() == []


class TestConcurrencyAndOutage:
    def test_racing_runners_one_wins_the_loser_writes_nothing_further(self, cp, tmp_path):
        """TS-17: a barrier holds both runners at the head commit; exactly one commits."""
        sid = run(_created(_runner(cp, tmp_path)[0]))
        a, _, _ = _runner(cp, tmp_path, name="a.json")
        b, _, _ = _runner(cp, tmp_path, name="b.json")
        barrier = threading.Barrier(2, timeout=5)
        state = {"armed": True}

        def hold_at_first_head_write(key, apply):
            if key == HEAD_KEY and state["armed"]:
                try:
                    barrier.wait()
                except threading.BrokenBarrierError:
                    pass
                state["armed"] = False
            return apply()

        cp.vault.on_set = hold_at_first_head_write

        async def race():
            return await asyncio.gather(
                a.run(CID, user_id="user-1", message="go"), b.run(CID, user_id="user-1", message="go")
            )

        outcomes = run(race())
        cp.vault.on_set = None

        statuses = sorted(o.status for o in outcomes)
        assert statuses == ["completed", "conflict"]
        loser = next(o for o in outcomes if o.status == "conflict")
        assert loser.session_id == sid
        final = run(_service(cp).get_session(app_name="app", user_id="user-1", session_id=sid))
        assert len([e for e in final.events if e.author == "user"]) == 1  # one lineage, no duplicate input

    def test_duplicate_register_reuses_the_existing_session(self, cp, tmp_path, monkeypatch):
        """TS-18: lookup misses (race), register gets 409 with the existing id, never a second session."""
        existing = run(_created(_runner(cp, tmp_path)[0]))
        runner, _, _ = _runner(cp, tmp_path, name="r.json")
        real_lookup = cp.sessions.find_by_correlation_id
        calls = {"n": 0}

        def racy_lookup(org, cid):
            calls["n"] += 1
            return None if calls["n"] == 1 else real_lookup(org, cid)

        monkeypatch.setattr(cp.sessions, "find_by_correlation_id", racy_lookup)

        outcome = run(runner.run(CID, user_id="user-1", message="go"))

        assert outcome.session_id == existing and outcome.status == "completed"
        assert len(cp.shared.sessions) == 1

    def test_transient_outage_stops_in_progress_and_a_later_run_resumes(self, cp, tmp_path):
        """TS-21: a 5xx mid-run is not retried and not a failure; the session stays IN_PROGRESS."""
        runner, _, tools = _runner(cp, tmp_path, script=steps_script(3))
        state = {"heads": 0}

        def fail_third_head_write(key, apply):
            if key == HEAD_KEY:
                state["heads"] += 1
                if state["heads"] == 3:  # create, user message, then the first model turn (step-1 not yet run)
                    raise http_error(503)
            return apply()

        cp.vault.on_set = fail_third_head_write
        with pytest.raises(errors.VaultUnavailable):
            run(runner.run(CID, user_id="user-1", message="go"))
        cp.vault.on_set = None
        sid = cp.sessions.find_by_correlation_id(2, CID).session_id
        assert cp.session(sid)["status"] == 2
        assert FAILURE_KEY not in cp.vault.keys(sid)

        outcome = run(runner.run(CID, user_id="user-1", message="go"))

        assert outcome.status == "completed"
        assert all(n == 1 for n in tools.counts().values())


class TestResumeInputSelection:
    def _events(self, *specs):
        return [Event(invocation_id=f"inv-{i}", author="agent", **spec) for i, spec in enumerate(specs)]

    def test_priority_answer_then_unfinished_then_message(self):
        """TS-52."""
        unfinished = self._events({"content": types.Content(role="model", parts=[types.Part(text="half")])})
        answer = types.Content(role="user", parts=[types.Part(text="answer")])

        assert choose_resume_input(unfinished, answers=answer, message=_msg("m")).new_message is answer
        chosen = choose_resume_input(unfinished, answers=None, message=_msg("m"))
        assert (chosen.invocation_id, chosen.new_message) == ("inv-0", None)
        fresh = choose_resume_input([], answers=None, message=_msg("m"))
        assert fresh.new_message.parts[0].text == "m" and fresh.invocation_id is None

    def test_finished_invocation_means_completed(self):
        from google.adk.events import EventActions

        done = self._events({"content": None, "actions": EventActions(end_of_agent=True)})
        assert choose_resume_input(done, answers=None, message=_msg("m")).finished is True


class TestKillAndResume:
    def test_sigkilled_run_resumes_without_repeating_completed_steps(self, tmp_path):
        """TS-19: steps 1-3 commit, the process is SIGKILLed as step 4 starts, a new process finishes."""
        backend, counts, out = tmp_path / "cp.json", tmp_path / "counts.json", tmp_path / "out.json"
        cmd = [sys.executable, "-m", "agency_sdk.test.adk_proc", "durable", str(backend), str(counts), CID]
        env = {**os.environ, "PYTHONWARNINGS": "ignore"}

        killed = subprocess.run([*cmd, "4", str(out)], cwd=REPO, env=env, timeout=120)
        assert killed.returncode == -9 and not out.exists()
        assert json.loads(counts.read_text()) == {"step-1": 1, "step-2": 1, "step-3": 1}

        subprocess.run([*cmd, "4", str(out)], cwd=REPO, env=env, timeout=120, check=True)

        outcome = json.loads(out.read_text())
        assert outcome["status"] == "completed"
        assert json.loads(counts.read_text()) == {f"step-{i}": 1 for i in range(1, 6)}
        cp = FakeControlPlane(backend)
        assert len(cp.shared.sessions) == 1 and cp.session(outcome["session_id"])["status"] == 0
