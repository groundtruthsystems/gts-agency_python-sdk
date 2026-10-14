"""TS-31: sentinel PHI never escapes through the SDK's logs, exception text or ``repr``s.

Sentinels are planted in every PHI-capable place — ``user_id``, correlation id, tool arguments, session
state, the human's answer and an exception message — and the run is driven through success, pause, resume,
failure and error paths. ADK's own models (``Session``, ``Event``) are out of scope: they are ADK's, and
the docs tell callers not to log them whole.
"""

import asyncio
import logging

import pytest

pytest.importorskip("google.adk")

from agency_sdk.adk import DurableRunner, GtsSessionService, errors  # noqa: E402
from agency_sdk.adk._records import FailureRecord, SessionHead  # noqa: E402
from agency_sdk.test.adk_fakes import FakeControlPlane, http_error  # noqa: E402
from agency_sdk.test.adk_llm_fakes import CountingTools, call, make_app, text  # noqa: E402

SENTINEL = "PHI-SENTINEL-0001"
USER = f"user-{SENTINEL}"
CID = f"corr-{SENTINEL}"


def run(coro):
    return asyncio.run(coro)


def _service(cp):
    return GtsSessionService(
        vault=cp.vault, sessions=cp.sessions, templates=cp.templates, organisation_id=2, template_name="adk-agent"
    )


def test_sentinel_never_reaches_sdk_logs_exceptions_or_reprs(tmp_path, caplog):
    cp = FakeControlPlane()
    tools = CountingTools(tmp_path / "c.json")

    def boom(n: int) -> dict:
        """Fail with PHI in the message."""
        raise ValueError(f"patient {SENTINEL} not found")

    script = [
        call("ask_human", "c1", question=f"approve treatment for {SENTINEL}?"),
        lambda r: text(f"approved {r[-1].response}"),
    ]
    rendered: list[str] = []
    with caplog.at_level(logging.DEBUG):
        service = _service(cp)
        runner = DurableRunner(
            app=make_app(script, [tools.ask_human()], state_output="answer"), session_service=service, hitl=cp.hitl
        )
        paused = run(runner.run(CID, user_id=USER, message=f"case of {SENTINEL}"))
        cp.hitl.respond(2, paused.session_id, "c1", {"note": SENTINEL})
        done = run(runner.resume(CID, user_id=USER))
        rendered += [repr(paused), str(paused), repr(done), str(done)]
        rendered += [repr(r) for r in cp.hitl.list(2, paused.session_id)]

        failing = DurableRunner(
            app=make_app([call("boom", "c9", n=1), text("x")], [boom]), session_service=_service(cp)
        )
        failed = run(failing.run(f"{CID}-2", user_id=USER, message=SENTINEL))
        rendered += [repr(failed), str(failed)]

        head = SessionHead(
            app_name="app", user_id=USER, correlation_id=CID, last_update_time=1.0, state={"x": SENTINEL}
        )
        rendered += [repr(head), str(head), repr(FailureRecord(error_type="ValueError", revision=1))]

        problems = []
        try:
            run(runner.resume(f"never-{SENTINEL}", user_id=USER))
        except errors.SessionNotFound as exc:
            problems.append(exc)
        cp.vault.on_set = lambda key, apply: (_ for _ in ()).throw(http_error(503))
        try:
            run(_service(cp).create_session(app_name="app", user_id=USER, correlation_id=CID + "-3"))
        except errors.VaultUnavailable as exc:
            problems.append(exc)
        cp.vault.on_set = None

    assert done.status == "completed" and failed.status == "failed" and len(problems) == 2
    sdk_logs = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("agency_sdk"))
    texts = rendered + [str(p) for p in problems] + [repr(p) for p in problems] + [sdk_logs]
    leaks = [t for t in texts if SENTINEL in t]
    assert leaks == []
