"""Offline tests for the Agent Vault record codec and ``VaultStore`` (Phase 2).

Scenarios (``test-plan.md``): TS-07 retry re-read, TS-13 schema, TS-32 classification, TS-35 decode safety,
TS-36 caps, TS-45 timeout, TS-55 bytes round-trip, TS-58 chain bounds, TS-59 parallel GETs, TS-61
cancellation. Everything runs against ``FakeVault`` — no network.
"""

import asyncio
import pathlib
import re
import threading

import pytest

pytest.importorskip("google.adk")

from google.adk.errors import StaleSessionError  # noqa: E402
from google.adk.events import Event  # noqa: E402
from google.genai import types  # noqa: E402

from agency_sdk.adk import errors  # noqa: E402
from agency_sdk.adk._records import (  # noqa: E402
    EVENT_PREFIX,
    FAILURE_KEY,
    HEAD_KEY,
    STATE_KEY,
    FailureRecord,
    SessionHead,
    decode_event,
    encode_event,
    event_key,
)
from agency_sdk.adk._store import VaultLimits, VaultStore  # noqa: E402
from agency_sdk.test.adk_fakes import FakeVault  # noqa: E402

SID = "sess-1"


def _event(i: int, text: str = "hello", **kw) -> Event:
    return Event(
        id=f"ev-{i:04d}",
        invocation_id="inv-1",
        author="user",
        timestamp=1000.0 + i,
        content=types.Content(role="user", parts=[types.Part(text=text)]),
        **kw,
    )


def _head(**kw) -> SessionHead:
    return SessionHead(app_name="app", user_id="user-1", last_update_time=1000.0, **kw)


@pytest.fixture
def vault():
    return FakeVault()


@pytest.fixture
def store(vault):
    return VaultStore(vault, organisation_id=2)


def run(coro):
    return asyncio.run(coro)


async def _seed(store, n: int):
    snap = await store.create_head(SID, _head())
    for i in range(1, n + 1):
        snap = await store.append_event(SID, snap, _event(i), state=snap.head.state, last_update_time=1000.0 + i)
    return snap


class TestKeysAndRecords:
    def test_keys_are_within_the_vault_charset(self):
        for key in (HEAD_KEY, FAILURE_KEY, STATE_KEY, event_key(1, "e-abc-123"), event_key(99999999, "x" * 200)):
            assert re.fullmatch(r"[A-Za-z0-9._-]{1,255}", key), key

    def test_event_keys_sort_by_sequence(self):
        assert event_key(2, "z") < event_key(10, "a")
        assert event_key(1, "a").startswith(EVENT_PREFIX)

    def test_record_reprs_never_show_state_or_user(self):
        head = _head(state={"diagnosis": "PHI-SENTINEL-0001"})
        rendered = repr(head) + str(head)
        assert "PHI-SENTINEL-0001" not in rendered and "user-1" not in rendered


class TestBytesRoundTrip:
    def test_inline_bytes_survive_exactly(self):
        """TS-55: bytes go out as base64 JSON and come back byte-identical via model_validate_json."""
        payload = bytes(range(256)) * 3
        ev = Event(
            id="ev-b",
            invocation_id="inv",
            author="user",
            content=types.Content(
                role="user", parts=[types.Part(inline_data=types.Blob(mime_type="image/png", data=payload))]
            ),
            long_running_tool_ids={"c1", "c2"},
        )

        stored = encode_event(ev)
        assert isinstance(stored["content"]["parts"][0]["inlineData"]["data"], str)  # base64 text, camelCase
        back = decode_event(stored)

        assert back.content.parts[0].inline_data.data == payload
        assert back.long_running_tool_ids == {"c1", "c2"}
        assert back.model_dump(by_alias=True) == ev.model_dump(by_alias=True)

    def test_none_fields_are_kept(self):
        stored = encode_event(_event(1))
        assert "branch" in stored and stored["branch"] is None


class TestHeadAndSchema:
    def test_create_and_read_head(self, store, vault):
        snap = run(store.create_head(SID, _head()))
        again = run(store.read_head(SID))

        assert again.version == snap.version == 1
        assert again.head.app_name == "app"
        assert vault.log[0].kwargs["if_absent"] is True

    def test_missing_head_is_none(self, store):
        assert run(store.read_head("nope")) is None

    def test_future_schema_fails_closed(self, store, vault):
        """TS-13."""
        run(store.create_head(SID, _head()))
        vault.data[(SID, None, HEAD_KEY)].value["schema"] = 99

        with pytest.raises(errors.UnsupportedSchema):
            run(store.read_head(SID))

    def test_malformed_head_is_a_typed_error(self, store, vault):
        """TS-35."""
        run(store.create_head(SID, _head()))
        vault.data[(SID, None, HEAD_KEY)].value = {"schema": 1, "revision": "not-an-int"}

        with pytest.raises(errors.CorruptRecord):
            run(store.read_head(SID))


class TestAppend:
    def test_event_then_head_with_preconditions(self, store, vault):
        snap = run(store.create_head(SID, _head()))
        vault.log.clear()

        new = run(store.append_event(SID, snap, _event(1), state={"k": 1}, last_update_time=1001.0))

        writes = vault.writes()
        assert [w.key for w in writes] == [event_key(1, "ev-0001"), HEAD_KEY]
        assert writes[0].kwargs["if_absent"] is True
        assert writes[1].kwargs["if_version"] == snap.version
        assert (new.head.revision, new.head.event_count, new.head.last_event_key) == (1, 1, event_key(1, "ev-0001"))
        assert new.head.state == {"k": 1}

    def test_stale_head_raises_and_writes_no_head(self, store, vault):
        snap = run(store.create_head(SID, _head()))
        run(store.append_event(SID, snap, _event(1), state={}, last_update_time=1.0))  # moves the head on

        with pytest.raises(StaleSessionError):
            run(store.append_event(SID, snap, _event(2), state={}, last_update_time=2.0))

        assert run(store.read_head(SID)).head.event_count == 1

    def test_lost_response_after_commit_is_success(self, store, vault):
        """TS-07: the head CAS committed, the response was lost, the retry got 412 — re-read says it was ours."""
        snap = run(store.create_head(SID, _head()))

        def commit_then_report_mismatch(key, apply):
            result = apply()
            if key == HEAD_KEY:
                vault.on_set = None
                from agency_sdk.delegates.session_vault_client import ConditionalWriteFailed

                raise ConditionalWriteFailed("version_mismatch")
            return result

        vault.on_set = commit_then_report_mismatch
        new = run(store.append_event(SID, snap, _event(1), state={}, last_update_time=1.0))

        assert new.head.event_count == 1
        assert len([k for k in vault.keys(SID) if k.startswith(EVENT_PREFIX)]) == 1

    def test_lost_event_response_after_commit_is_success(self, store, vault):
        """TS-07 (event leg): an if_absent 409 on our own event id is ours, not a conflict."""
        snap = run(store.create_head(SID, _head()))

        def commit_then_report_exists(key, apply):
            result = apply()
            if key.startswith(EVENT_PREFIX):
                vault.on_set = None
                from agency_sdk.delegates.session_vault_client import ConditionalWriteFailed

                raise ConditionalWriteFailed("exists")
            return result

        vault.on_set = commit_then_report_exists
        new = run(store.append_event(SID, snap, _event(1), state={}, last_update_time=1.0))

        assert new.head.event_count == 1

    def test_calls_carry_the_10s_timeout(self, store, vault):
        """TS-45 (store leg)."""
        snap = run(store.create_head(SID, _head()))
        run(store.append_event(SID, snap, _event(1), state={}, last_update_time=1.0))
        run(store.read_head(SID))

        assert {c.kwargs["timeout"] for c in vault.log} == {10.0}


class TestClassification:
    def test_fixed_classification_per_key_kind(self, store, vault):
        """TS-32."""
        snap = run(store.create_head(SID, _head()))
        run(store.append_event(SID, snap, _event(1), state={}, last_update_time=1.0))
        run(store.write_failure(SID, FailureRecord(error_type="ValueError", invocation_id="inv", revision=1)))
        run(store.merge_scoped(SID, "user", {"pref": 1}))
        run(store.merge_scoped(SID, "app", {"flag": 1}))

        by_key = {(c.key, c.scope): c.kwargs["classification"] for c in vault.writes()}
        assert set(by_key.values()) == {"restricted"}

    def test_app_scope_may_opt_into_internal(self, vault):
        store = VaultStore(vault, organisation_id=2, app_state_classification="internal")
        run(store.merge_scoped(SID, "app", {"flag": 1}))

        assert vault.writes()[-1].kwargs["classification"] == "internal"

    @pytest.mark.parametrize("level", ["public", "confidential", "bogus"])
    def test_app_scope_rejects_other_levels(self, vault, level):
        with pytest.raises(ValueError):
            VaultStore(vault, organisation_id=2, app_state_classification=level)


class TestLimits:
    def test_oversize_event_is_refused_before_any_put(self, vault):
        """TS-36."""
        store = VaultStore(vault, organisation_id=2, limits=VaultLimits(max_value_bytes=2_000))
        snap = run(store.create_head(SID, _head()))
        vault.log.clear()

        with pytest.raises(errors.VaultLimitExceeded):
            run(store.append_event(SID, snap, _event(1, text="x" * 5_000), state={}, last_update_time=1.0))

        assert vault.writes() == []

    def test_oversize_head_state_is_refused_before_any_put(self, vault):
        store = VaultStore(vault, organisation_id=2, limits=VaultLimits(max_value_bytes=2_000))
        snap = run(store.create_head(SID, _head()))
        vault.log.clear()

        with pytest.raises(errors.VaultLimitExceeded):
            run(store.append_event(SID, snap, _event(1), state={"blob": "x" * 5_000}, last_update_time=1.0))

        assert vault.writes() == []

    def test_event_bound_is_enforced(self, vault):
        store = VaultStore(vault, organisation_id=2, limits=VaultLimits(max_events=2))
        snap = run(_seed(store, 2))
        vault.log.clear()

        with pytest.raises(errors.VaultLimitExceeded):
            run(store.append_event(SID, snap, _event(3), state={}, last_update_time=3.0))

        assert vault.writes() == []

    def test_non_json_state_is_rejected(self, store, vault):
        snap = run(store.create_head(SID, _head()))

        with pytest.raises(errors.AgentVaultError):
            run(store.append_event(SID, snap, _event(1), state={"when": object()}, last_update_time=1.0))


class TestRead:
    def test_full_read_returns_events_in_order(self, store):
        snap = run(_seed(store, 5))

        events = run(store.read_events(SID, snap.head))

        assert [e.id for e in events] == [f"ev-{i:04d}" for i in range(1, 6)]

    def test_recent_read_walks_back_with_bounded_gets(self, store, vault):
        snap = run(_seed(store, 10))
        vault.log.clear()

        events = run(store.read_events(SID, snap.head, num_recent_events=3))

        assert [e.id for e in events] == ["ev-0008", "ev-0009", "ev-0010"]
        assert len([c for c in vault.log if c.op == "get"]) <= 3

    def test_after_timestamp_filters(self, store):
        snap = run(_seed(store, 6))

        events = run(store.read_events(SID, snap.head, after_timestamp=1004.0))

        assert [e.id for e in events] == ["ev-0004", "ev-0005", "ev-0006"]

    def test_orphan_events_are_ignored(self, store, vault):
        snap = run(_seed(store, 2))
        # A CAS loser's event: written, never committed to the head.
        orphan = {"seq": 3, "prev_key": snap.head.last_event_key, "event": encode_event(_event(3, text="orphan"))}
        vault.data[(SID, None, event_key(3, "ev-orphan"))] = type(vault.data[(SID, None, HEAD_KEY)])(
            orphan, "restricted", 1
        )

        events = run(store.read_events(SID, snap.head))

        assert [e.id for e in events] == ["ev-0001", "ev-0002"]

    def test_cyclic_chain_is_a_typed_error(self, store, vault):
        """TS-58."""
        snap = run(_seed(store, 3))
        first = event_key(1, "ev-0001")
        vault.data[(SID, None, first)].value["prev_key"] = snap.head.last_event_key  # 1 -> 3 -> 2 -> 1 ...

        with pytest.raises(errors.CorruptRecord):
            run(store.read_events(SID, snap.head))
        with pytest.raises(errors.CorruptRecord):
            run(store.read_events(SID, snap.head, num_recent_events=10))

    def test_truncated_chain_is_a_typed_error(self, store, vault):
        snap = run(_seed(store, 3))
        del vault.data[(SID, None, event_key(2, "ev-0002"))]

        with pytest.raises(errors.CorruptRecord):
            run(store.read_events(SID, snap.head))

    def test_parallel_gets_are_capped_at_8(self, store, vault):
        """TS-59."""
        snap = run(_seed(store, 40))
        vault.get_delay = 0.01
        vault.peak_inflight = 0

        events = run(store.read_events(SID, snap.head))

        assert len(events) == 40
        assert 1 < vault.peak_inflight <= 8


class TestScopedAndFailure:
    def test_scoped_merge_is_per_key(self, store, vault):
        run(store.merge_scoped(SID, "user", {"a": 1, "b": 2}))
        run(store.merge_scoped(SID, "user", {"b": 3}))

        assert run(store.read_scoped(SID, "user")) == {"a": 1, "b": 3}
        assert run(store.read_scoped(SID, "app")) == {}

    def test_scoped_merge_retries_on_conflict(self, store, vault):
        run(store.merge_scoped(SID, "user", {"a": 1}))
        calls = {"n": 0}

        def race_once(key, apply):
            if key == STATE_KEY and calls["n"] == 0:
                calls["n"] += 1
                vault.on_set = None
                vault.data[(SID, "user", STATE_KEY)].value = {"state": {"a": 1, "other": 9}}
                vault.data[(SID, "user", STATE_KEY)].version += 1
                return apply()  # raises version_mismatch against the moved record
            return apply()

        vault.on_set = race_once
        run(store.merge_scoped(SID, "user", {"b": 2}))

        assert run(store.read_scoped(SID, "user")) == {"a": 1, "other": 9, "b": 2}

    def test_failure_record_is_idempotent(self, store, vault):
        rec = FailureRecord(error_type="ValueError", invocation_id="inv", revision=3)
        run(store.write_failure(SID, rec))
        run(store.write_failure(SID, FailureRecord(error_type="KeyError", invocation_id="inv", revision=4)))

        assert run(store.read_failure(SID)).error_type == "ValueError"


class TestCancellation:
    def test_cancelled_append_finishes_and_the_next_call_waits_for_it(self, store, vault):
        """TS-61."""

        async def scenario():
            snap = await store.create_head(SID, _head())
            gate = threading.Event()

            def slow_head(key, apply):
                if key == HEAD_KEY:
                    gate.wait(2.0)
                return apply()

            vault.on_set = slow_head
            task = asyncio.create_task(store.append_event(SID, snap, _event(1), state={}, last_update_time=1.0))
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            gate.set()
            vault.on_set = None
            return await store.read_head(SID)

        head = run(scenario())

        assert head.head.event_count == 1
        assert len([k for k in vault.keys(SID) if k.startswith(EVENT_PREFIX)]) == 1


class TestCodeSafety:
    def test_no_pickle_eval_or_exec_in_the_adk_package(self):
        """TS-35 (static leg)."""
        root = pathlib.Path(__file__).resolve().parents[1] / "adk"
        source = "\n".join(p.read_text() for p in root.rglob("*.py"))
        assert not re.search(r"\b(pickle|marshal|eval|exec)\s*[.(]", source)
