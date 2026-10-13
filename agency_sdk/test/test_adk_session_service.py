"""Offline tests for ``GtsSessionService`` (Phase 3: session storage).

Scenarios (``test-plan.md``): TS-01 drop-in parity, TS-02 lazy import + ``gts://``, TS-03/04 create, TS-05/06
append + stale, TS-08 ``temp:``, TS-09 cross-process exact read-back, TS-11 filters, TS-12 not found, TS-14
list/delete, TS-15 scoped state, TS-34 scope never client-addressable, TS-39 ADK contract, TS-41 contention,
TS-42 orphans, TS-43 request budget, TS-57 scoped repair, TS-68 unbound owner fails closed.
"""

import asyncio
import inspect
import json
import os
import subprocess
import sys
from importlib import metadata

import pytest

pytest.importorskip("google.adk")

from google.adk.errors import StaleSessionError  # noqa: E402
from google.adk.errors.already_exists_error import AlreadyExistsError  # noqa: E402
from google.adk.events import Event, EventActions  # noqa: E402
from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import BaseSessionService, InMemorySessionService, Session  # noqa: E402
from google.adk.sessions.base_session_service import GetSessionConfig  # noqa: E402
from google.genai import types  # noqa: E402

from agency_sdk.adk import GtsSessionService, errors  # noqa: E402
from agency_sdk.adk._records import EVENT_PREFIX, HEAD_KEY, STATE_KEY, encode_event, event_key  # noqa: E402
from agency_sdk.delegates.session_templates_client import SessionTemplateNotFound  # noqa: E402
from agency_sdk.test.adk_fakes import FakeControlPlane  # noqa: E402
from agency_sdk.test.adk_llm_fakes import CountingTools, make_app, steps_script  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def cp():
    return FakeControlPlane()


def _service(cp, **kw) -> GtsSessionService:
    return GtsSessionService(
        vault=cp.vault, sessions=cp.sessions, templates=cp.templates, organisation_id=2, template_name="adk-agent", **kw
    )


@pytest.fixture
def svc(cp):
    return _service(cp)


def _event(text="hi", delta=None, ts=None, **kw) -> Event:
    return Event(
        invocation_id="inv-1",
        author="user",
        content=types.Content(role="user", parts=[types.Part(text=text)]),
        actions=EventActions(state_delta=delta or {}),
        **({"timestamp": ts} if ts is not None else {}),
        **kw,
    )


async def _new(svc, cp, **kw) -> Session:
    return await svc.create_session(app_name="app", user_id="user-1", **kw)


class TestCreate:
    def test_create_without_id_registers_from_the_template(self, svc, cp):
        """TS-03."""
        session = run(_new(svc, cp, state={"k": 1}))

        reg = [entry for op, entry in cp.log if op == "register"]
        assert reg == [{"template": "adk-agent", "labels": {"adk_app": "app"}, "owner": "user-1"}]
        assert cp.session(session.id)["labels"]["correlation_id"]
        head = cp.vault.value(session.id, HEAD_KEY)
        assert (head["revision"], head["event_count"], head["state"]) == (0, 0, {"k": 1})
        assert session.state == {"k": 1} and session.events == []
        assert session._storage_update_marker == "1"

    def test_create_with_a_dispatched_id_adopts_it(self, svc, cp):
        """TS-04: adopt via update(labels=), never register."""
        cp.add_session("s-dispatched", owner="user-1")

        session = run(svc.create_session(app_name="app", user_id="user-1", session_id="s-dispatched"))

        assert session.id == "s-dispatched"
        assert not [op for op, _ in cp.log if op == "register"]
        assert cp.session("s-dispatched")["labels"]["adk_app"] == "app"

    def test_create_on_an_existing_head_raises(self, svc, cp):
        cp.add_session("s-1", owner="user-1")
        run(svc.create_session(app_name="app", user_id="user-1", session_id="s-1"))

        with pytest.raises(AlreadyExistsError):
            run(svc.create_session(app_name="app", user_id="user-1", session_id="s-1"))

    def test_unknown_template_is_typed_and_writes_nothing(self, cp):
        svc = GtsSessionService(
            vault=cp.vault, sessions=cp.sessions, templates=cp.templates, organisation_id=2, template_name="nope"
        )

        with pytest.raises(SessionTemplateNotFound):
            run(_new(svc, cp))

        assert cp.vault.writes() == []

    def test_create_without_id_and_without_template_is_refused(self, cp):
        svc = GtsSessionService(vault=cp.vault, sessions=cp.sessions, templates=cp.templates, organisation_id=2)

        with pytest.raises(ValueError):
            run(_new(svc, cp))

    def test_initial_scoped_state_is_routed_to_scopes(self, svc, cp):
        session = run(_new(svc, cp, state={"k": 1, "app:a": 2, "user:u": 3, "temp:t": 4}))

        assert cp.vault.value(session.id, HEAD_KEY)["state"] == {"k": 1}
        assert cp.vault.value(session.id, STATE_KEY, scope="app")["state"] == {"a": 2}
        assert cp.vault.value(session.id, STATE_KEY, scope="user")["state"] == {"u": 3}
        assert session.state == {"k": 1, "app:a": 2, "user:u": 3}


class TestAppend:
    def test_append_commits_event_then_head(self, svc, cp):
        """TS-05."""
        session = run(_new(svc, cp))
        cp.vault.log.clear()

        event = run(svc.append_event(session, _event(delta={"k": 1, "temp:t": 9})))

        writes = [(c.key, c.kwargs["if_absent"], c.kwargs["if_version"]) for c in cp.vault.writes()]
        assert writes[0][0].startswith(EVENT_PREFIX) and writes[0][1] is True
        assert writes[1] == (HEAD_KEY, False, 1)
        assert event.id and session.events[-1] is event
        assert session._storage_update_marker == "2"
        assert cp.vault.value(session.id, HEAD_KEY)["state"] == {"k": 1}

    def test_partial_events_are_not_persisted(self, svc, cp):
        session = run(_new(svc, cp))
        cp.vault.log.clear()

        run(svc.append_event(session, _event(partial=True)))

        assert cp.vault.writes() == [] and session.events == []

    def test_temp_state_is_in_memory_only(self, svc, cp):
        """TS-08."""
        session = run(_new(svc, cp))

        run(svc.append_event(session, _event(delta={"temp:t": 9, "k": 1})))

        assert session.state["temp:t"] == 9
        stored = cp.vault.value(session.id, HEAD_KEY)
        assert "temp:t" not in stored["state"]
        ev = cp.vault.value(session.id, stored["last_event_key"])
        assert "temp:t" not in json.dumps(ev)

    def test_stale_session_changes_nothing(self, svc, cp):
        """TS-06."""
        session = run(_new(svc, cp))
        other = run(svc.get_session(app_name="app", user_id="user-1", session_id=session.id))
        run(svc.append_event(other, _event(text="first")))
        before = (list(session.events), dict(session.state), session._storage_update_marker)
        cp.vault.log.clear()

        with pytest.raises(StaleSessionError):
            run(svc.append_event(session, _event(text="second", delta={"k": 1, "user:u": 1})))

        assert (session.events, session.state, session._storage_update_marker) == before
        assert not [c for c in cp.vault.writes() if c.scope is not None]
        assert cp.vault.value(session.id, HEAD_KEY)["event_count"] == 1

    def test_concurrent_appends_one_winner_no_lost_events(self, svc, cp):
        """TS-41: N copies race one revision; exactly one commits, the rest are stale, the chain is intact."""
        session = run(_new(svc, cp))

        async def race():
            copies = [await svc.get_session(app_name="app", user_id="user-1", session_id=session.id) for _ in range(5)]
            return await asyncio.gather(
                *(svc.append_event(c, _event(text=f"t{i}")) for i, c in enumerate(copies)), return_exceptions=True
            )

        results = run(race())

        assert sum(isinstance(r, Event) for r in results) == 1
        assert all(isinstance(r, (Event, StaleSessionError)) for r in results)
        reread = run(svc.get_session(app_name="app", user_id="user-1", session_id=session.id))
        assert len(reread.events) == 1

    def test_orphan_from_a_crash_is_ignored_and_the_next_append_succeeds(self, svc, cp):
        """TS-42."""
        session = run(_new(svc, cp))
        run(svc.append_event(session, _event(text="one")))
        orphan = {"seq": 2, "prev_key": session.events[-1] and cp.vault.value(session.id, HEAD_KEY)["last_event_key"]}
        orphan["event"] = encode_event(Event(id="ev-orphan", invocation_id="x", author="user"))
        cp.vault.set(2, session.id, event_key(2, "ev-orphan"), orphan)

        reread = run(svc.get_session(app_name="app", user_id="user-1", session_id=session.id))
        assert [e.content.parts[0].text for e in reread.events] == ["one"]
        run(svc.append_event(reread, _event(text="two")))
        assert len(run(svc.get_session(app_name="app", user_id="user-1", session_id=session.id)).events) == 2


class TestGet:
    def test_unknown_or_foreign_session_is_none(self, svc, cp):
        """TS-12."""
        session = run(_new(svc, cp))

        assert run(svc.get_session(app_name="app", user_id="user-1", session_id="nope")) is None
        assert run(svc.get_session(app_name="other", user_id="user-1", session_id=session.id)) is None
        assert run(svc.get_session(app_name="app", user_id="user-2", session_id=session.id)) is None

    def test_get_session_config_filters(self, svc, cp):
        """TS-11."""
        session = run(_new(svc, cp))
        for i in range(10):
            run(svc.append_event(session, _event(text=f"t{i}", ts=1000.0 + i)))

        recent = run(
            svc.get_session(
                app_name="app", user_id="user-1", session_id=session.id, config=GetSessionConfig(num_recent_events=3)
            )
        )
        after = run(
            svc.get_session(
                app_name="app", user_id="user-1", session_id=session.id, config=GetSessionConfig(after_timestamp=1007.0)
            )
        )
        none = run(
            svc.get_session(
                app_name="app", user_id="user-1", session_id=session.id, config=GetSessionConfig(num_recent_events=0)
            )
        )

        assert [e.content.parts[0].text for e in recent.events] == ["t7", "t8", "t9"]
        assert [e.content.parts[0].text for e in after.events] == ["t7", "t8", "t9"]
        assert none.events == []

    def test_get_session_never_writes(self, svc, cp):
        session = run(_new(svc, cp, state={"app:a": 1, "user:u": 2}))
        run(svc.append_event(session, _event(delta={"k": 1})))
        cp.vault.log.clear()

        run(svc.get_session(app_name="app", user_id="user-1", session_id=session.id))

        assert cp.vault.writes() == []
        assert not any(c.kwargs.get("reveal") for c in cp.vault.log)

    def test_request_budget(self, svc, cp):
        """TS-43: a checkpoint is 2 PUTs; a full hydrate is head + 1 list + N event GETs + 2 scoped GETs."""
        session = run(_new(svc, cp))
        for i in range(49):
            run(svc.append_event(session, _event(text=f"t{i}")))
        cp.vault.log.clear()
        run(svc.append_event(session, _event(text="t49")))
        assert [c.op for c in cp.vault.log] == ["set", "set"]

        cp.vault.log.clear()
        run(svc.get_session(app_name="app", user_id="user-1", session_id=session.id))
        ops = [c.op for c in cp.vault.log]
        assert ops.count("list") == 1 and ops.count("get") == 1 + 50 + 2


class TestListAndDelete:
    def test_list_by_app_and_user_then_delete(self, svc, cp):
        """TS-14."""
        a = run(svc.create_session(app_name="app", user_id="user-1"))
        b = run(svc.create_session(app_name="app", user_id="user-2"))
        run(svc.create_session(app_name="other", user_id="user-1"))

        assert {s.id for s in run(svc.list_sessions(app_name="app")).sessions} == {a.id, b.id}
        mine = run(svc.list_sessions(app_name="app", user_id="user-1")).sessions
        assert [s.id for s in mine] == [a.id] and mine[0].events == [] and mine[0].state == {}

        run(svc.delete_session(app_name="app", user_id="user-1", session_id=a.id))
        run(svc.delete_session(app_name="app", user_id="user-1", session_id=a.id))  # idempotent

        assert run(svc.get_session(app_name="app", user_id="user-1", session_id=a.id)) is None
        assert HEAD_KEY in cp.vault.keys(a.id)  # tombstoned, history kept
        assert {s.id for s in run(svc.list_sessions(app_name="app")).sessions} == {b.id}

    def test_list_is_oldest_updated_first(self, svc, cp):
        a = run(svc.create_session(app_name="app", user_id="u"))
        b = run(svc.create_session(app_name="app", user_id="u"))
        run(svc.append_event(a, _event(ts=9_999_999_999.0)))

        assert [s.id for s in run(svc.list_sessions(app_name="app")).sessions] == [b.id, a.id]


class TestScopedState:
    def test_app_and_user_state_cross_sessions_and_survive_delete(self, svc, cp):
        """TS-15."""
        one = run(svc.create_session(app_name="app", user_id="user-1"))
        run(svc.append_event(one, _event(delta={"app:theme": "dark", "user:lang": "en", "k": 1})))
        two = run(svc.create_session(app_name="app", user_id="user-1"))
        run(svc.delete_session(app_name="app", user_id="user-1", session_id=one.id))

        fresh = run(svc.get_session(app_name="app", user_id="user-1", session_id=two.id))

        assert fresh.state == {"app:theme": "dark", "user:lang": "en"}

    def test_get_user_state_is_not_implemented(self, svc):
        with pytest.raises(NotImplementedError):
            run(svc.get_user_state(app_name="app", user_id="user-1"))

    def test_scope_is_never_client_addressable(self, svc, cp):
        """TS-34: scoped calls carry only scope=app|user; no API takes a user or app identity for scope."""
        session = run(_new(svc, cp))
        run(svc.append_event(session, _event(delta={"user:x": 1, "app:y": 2})))

        scoped = [c for c in cp.vault.log if c.scope is not None]
        assert scoped and {c.scope for c in scoped} == {"app", "user"}
        assert all(c.session_id == session.id for c in scoped)
        from agency_sdk.adk._store import VaultStore

        for fn in (VaultStore.read_scoped, VaultStore.merge_scoped):
            assert "user" not in "".join(inspect.signature(fn).parameters)

    def test_unbound_owner_fails_closed(self, cp):
        """TS-68: no bound owner -> the server refuses user scope -> typed error, nothing written, no app fallback."""
        cp.add_session("s-anon", owner=None)
        svc = _service(cp)

        with pytest.raises(errors.VaultUnavailable) as exc:
            run(svc.create_session(app_name="app", user_id="user-1", session_id="s-anon"))

        assert exc.value.status_code == 403
        assert cp.vault.writes() == []

    def test_owner_unbound_after_create_fails_closed_on_read_and_append(self, svc, cp):
        cp.add_session("s-1", owner="user-1")
        session = run(svc.create_session(app_name="app", user_id="user-1", session_id="s-1"))
        with cp.shared.io():
            cp.shared.sessions["s-1"]["owner"] = None
        cp.vault.log.clear()

        with pytest.raises(errors.VaultUnavailable):
            run(svc.get_session(app_name="app", user_id="user-1", session_id="s-1"))
        with pytest.raises(errors.VaultUnavailable):
            run(svc.append_event(session, _event(delta={"user:x": 1})))
        assert not [c for c in cp.vault.writes() if c.scope == "app"]

    def test_register_sends_the_owner_claim(self, svc, cp):
        run(_new(svc, cp))
        assert [e["owner"] for op, e in cp.log if op == "register"] == ["user-1"]

    def test_missed_scoped_delta_is_repaired_by_the_next_run_not_by_reads(self, svc, cp, monkeypatch):
        """TS-57."""
        session = run(_new(svc, cp))
        original = svc._store.merge_scoped

        async def crash(*args, **kwargs):
            raise RuntimeError("process died after the head commit")

        monkeypatch.setattr(svc._store, "merge_scoped", crash)
        with pytest.raises(RuntimeError):
            run(svc.append_event(session, _event(delta={"user:lang": "fr"})))
        monkeypatch.setattr(svc._store, "merge_scoped", original)

        cp.vault.log.clear()
        reread = run(svc.get_session(app_name="app", user_id="user-1", session_id=session.id))
        assert "user:lang" not in reread.state and cp.vault.writes() == []

        run(svc.repair_scoped_state(reread))
        assert run(svc.get_session(app_name="app", user_id="user-1", session_id=session.id)).state["user:lang"] == "fr"


class TestParity:
    def test_same_script_same_outcome_as_in_memory(self, cp, tmp_path):
        """TS-01: swapping the session service changes nothing observable."""

        async def drive(service):
            tools = CountingTools(tmp_path / f"{type(service).__name__}.json")
            app = make_app(steps_script(3), [tools.step_tool()], state_output="answer")
            runner = Runner(app=app, session_service=service)
            session = await service.create_session(app_name="app", user_id="user-1")
            msg = types.Content(role="user", parts=[types.Part(text="go")])
            async for _ in runner.run_async(user_id="user-1", session_id=session.id, new_message=msg):
                pass
            final = await service.get_session(app_name="app", user_id="user-1", session_id=session.id)
            shape = [
                (e.author, json.dumps(e.content.model_dump(mode="json") if e.content else None), e.actions.state_delta)
                for e in final.events
            ]
            return shape, final.state, tools.counts()

        mem = run(drive(InMemorySessionService()))
        gts = run(drive(_service(cp)))

        assert gts == mem


class TestCrossProcess:
    def test_another_process_reads_the_session_back_exactly(self, tmp_path):
        """TS-09."""
        backend, wrote, read = tmp_path / "cp.json", tmp_path / "w.json", tmp_path / "r.json"
        env = {**os.environ, "PYTHONWARNINGS": "ignore"}
        cmd = [sys.executable, "-m", "agency_sdk.test.adk_proc"]
        subprocess.run([*cmd, "write", str(backend), str(wrote)], check=True, cwd=REPO, env=env, timeout=120)
        session_id = json.loads(wrote.read_text())["id"]
        subprocess.run([*cmd, "read", str(backend), session_id, str(read)], check=True, cwd=REPO, env=env, timeout=120)

        assert read.read_text() == wrote.read_text()


class TestPackagingAndContract:
    def test_core_import_never_loads_adk_and_missing_extra_names_it(self):
        """TS-02 (lazy import + actionable error)."""
        probe = "import sys, agency_sdk, agency_sdk.client; print('google.adk' in sys.modules)"
        out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, cwd=REPO, check=True)
        assert out.stdout.strip() == "False"

        blocked = "import sys; sys.modules['google.adk'] = None\ntry:\n import agency_sdk.adk\nexcept ImportError as e:\n print(type(e).__name__, '[adk]' in str(e))"
        out = subprocess.run([sys.executable, "-c", blocked], capture_output=True, text=True, cwd=REPO, check=True)
        assert out.stdout.strip() == "AdkNotInstalled True"

    def test_gts_uri_resolves_through_the_adk_registry(self, monkeypatch):
        """TS-02 (configuration-only switch)."""
        from google.adk.cli.service_registry import get_service_registry

        from agency_sdk.adk import register_gts_session_service

        monkeypatch.setenv("AGENCY_AUTH_URL", "http://auth.test/token")
        monkeypatch.setenv("AGENCY_CLIENT_ID", "id")
        monkeypatch.setenv("AGENCY_CLIENT_SECRET", "secret")
        register_gts_session_service()

        service = get_service_registry().create_session_service("gts://cp.test:13001?org=2&template=adk-agent&tls=0")

        assert isinstance(service, GtsSessionService)
        assert service.organisation_id == 2 and service.template_name == "adk-agent"
        assert service.base_url == "http://cp.test:13001"

    def test_implements_the_installed_adk_contract(self):
        """TS-39."""
        version = tuple(int(p) for p in metadata.version("google-adk").split(".")[:2])
        assert (2, 11) <= version < (3, 0)
        abstract = {n for n, m in inspect.getmembers(BaseSessionService) if getattr(m, "__isabstractmethod__", False)}
        assert abstract <= set(vars(GtsSessionService)), abstract - set(vars(GtsSessionService))
        for name in abstract | {"append_event"}:
            base = list(inspect.signature(getattr(BaseSessionService, name)).parameters)
            mine = list(inspect.signature(getattr(GtsSessionService, name)).parameters)
            assert mine[: len(base)] == base, name
        assert "_storage_update_marker" in Session.__private_attributes__
        assert callable(BaseSessionService._commit_event_to_session)
