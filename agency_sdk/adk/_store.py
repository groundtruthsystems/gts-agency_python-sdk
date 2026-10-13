"""``VaultStore``: the single async seam between ADK and the sync session-vault client.

ADK's session API is async; the SDK's delegates are sync ``requests`` clients. Every vault call goes
through :meth:`VaultStore._call`, which runs the sync call in ``asyncio.to_thread`` with a 10 s timeout,
leaves ``ConditionalWriteFailed`` as control flow, and turns every other failure into a URL-free
:class:`~agency_sdk.adk.errors.VaultUnavailable` (``requests`` error text carries request URLs).

Commit protocol for one checkpoint (:meth:`append_event`):

1. PUT the event record with ``if_absent``.
2. PUT the head with ``if_version`` — the only commit point.

A 409/412 after a connection retry may mean *our own* first attempt committed, so both legs re-read
before deciding: if the stored event id or head ``last_event_key`` is ours, the write succeeded;
otherwise the head moved on and ADK's ``StaleSessionError`` is raised, having changed nothing visible.

The write sequence is ``asyncio.shield``-ed: cancelling the caller cannot abandon a half-sent write.
The shielded task is remembered per session, and the next operation on that session waits for it, so
nothing is ever written against a head whose state is unknown.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

import requests
from google.adk.errors import StaleSessionError
from google.adk.errors.already_exists_error import AlreadyExistsError
from google.adk.events import Event

from agency_sdk.adk._records import (
    EVENT_PREFIX,
    FAILURE_KEY,
    HEAD_KEY,
    STATE_KEY,
    EventRecord,
    FailureRecord,
    SessionHead,
    decode_event,
    decode_event_record,
    decode_failure,
    decode_head,
    encode_event,
    event_key,
    to_wire,
)
from agency_sdk.adk.errors import AgentVaultError, CorruptRecord, InvalidState, VaultLimitExceeded, VaultUnavailable
from agency_sdk.delegates.session_vault_client import AgencySessionVaultClient, ConditionalWriteFailed

T = TypeVar("T")

RESTRICTED = "restricted"
_APP_STATE_LEVELS = frozenset({"restricted", "internal"})
_LIST_PAGE_SIZE = 500
_SCOPED_MERGE_ATTEMPTS = 5


@dataclass(frozen=True)
class VaultLimits:
    """Client-side caps, enforced before any request (aligned once comand publishes its real limits)."""

    max_value_bytes: int = 1 << 20
    max_events: int = 10_000


@dataclass(frozen=True)
class HeadSnapshot:
    """A head as read or written, with the vault ``version`` that is its CAS precondition."""

    head: SessionHead
    version: int


class _NotFound(Exception):
    pass


class VaultStore:
    """Async access to one organisation's ADK session records in the GTS session vault."""

    def __init__(
        self,
        vault: AgencySessionVaultClient,
        organisation_id: int,
        *,
        timeout: float = 10.0,
        limits: VaultLimits | None = None,
        app_state_classification: str = RESTRICTED,
        max_parallel: int = 8,
    ) -> None:
        """
        Args:
            vault: The session-vault delegate (``AgencyClient.session_vault()``).
            organisation_id: The organisation every session belongs to.
            timeout: Per-request timeout in seconds.
            limits: Size and event-count caps.
            app_state_classification: ``restricted`` (default) or, by explicit opt-in, ``internal`` for
                ``app:`` state. Every other record is always ``restricted``.
            max_parallel: Concurrent GETs when hydrating a full session.

        Raises:
            ValueError: If ``app_state_classification`` is not ``restricted`` or ``internal``.
        """
        if app_state_classification not in _APP_STATE_LEVELS:
            raise ValueError("app_state_classification must be 'restricted' or 'internal'")
        self._vault = vault
        self._org = organisation_id
        self._timeout = timeout
        self._limits = limits or VaultLimits()
        self._app_cls = app_state_classification
        self._semaphore = asyncio.Semaphore(max_parallel)
        self._inflight: dict[str, asyncio.Future[Any]] = {}

    # -- plumbing ------------------------------------------------------------------------------------
    async def _call(self, op: str, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        try:
            return await asyncio.to_thread(fn, self._org, *args, timeout=self._timeout, **kwargs)
        except ConditionalWriteFailed:
            raise
        except requests.HTTPError as err:
            status = err.response.status_code if err.response is not None else None
            if status == 404:
                raise _NotFound() from None
            raise VaultUnavailable(f"vault {op} failed with HTTP {status}", status_code=status) from None
        except requests.RequestException as err:
            raise VaultUnavailable(f"vault {op} failed ({type(err).__name__})") from None

    async def _settle(self, session_id: str) -> None:
        """Wait for a write a cancelled caller left in flight on this session."""
        pending = self._inflight.get(session_id)
        if pending is not None:
            try:
                await asyncio.shield(pending)
            # Swallowed on purpose: the caller's next read decides what actually committed.
            except Exception:  # nosec B110
                pass
            finally:
                if pending.done():
                    self._inflight.pop(session_id, None)

    def _check_size(self, value: Any, what: str) -> None:
        try:
            size = len(json.dumps(value).encode())
        except (TypeError, ValueError):
            raise InvalidState(f"{what} holds a value that is not JSON-serialisable") from None
        if size > self._limits.max_value_bytes:
            raise VaultLimitExceeded(f"{what} is {size} bytes; the limit is {self._limits.max_value_bytes}")

    async def _get(self, session_id: str, key: str, *, scope: str | None = None) -> tuple[Any, int] | None:
        try:
            entry = await self._call("get", self._vault.get, session_id, key, scope=scope)
        except _NotFound:
            return None
        return entry.value, entry.version or 0

    async def _put(self, session_id: str, key: str, value: Any, classification: str, **conditions: Any) -> int:
        result = await self._call(
            "put", self._vault.set, session_id, key, value, classification=classification, **conditions
        )
        return result.version or 0

    # -- heads ---------------------------------------------------------------------------------------
    async def _read_head_raw(self, session_id: str) -> HeadSnapshot | None:
        found = await self._get(session_id, HEAD_KEY)
        if found is None:
            return None
        value, version = found
        return HeadSnapshot(decode_head(value), version)

    async def read_head(self, session_id: str) -> HeadSnapshot | None:
        """The session head and its version, or ``None`` if the session has no ADK head."""
        await self._settle(session_id)
        return await self._read_head_raw(session_id)

    async def create_head(self, session_id: str, head: SessionHead) -> HeadSnapshot:
        """Write the first head (``if_absent``).

        Raises:
            AlreadyExistsError: The session already has an ADK head.
        """
        await self._settle(session_id)
        value = to_wire(head)
        self._check_size(value, "session head")
        try:
            version = await self._put(session_id, HEAD_KEY, value, RESTRICTED, if_absent=True)
        except ConditionalWriteFailed:
            raise AlreadyExistsError(f"Session {session_id} already exists.") from None
        return HeadSnapshot(head, version)

    async def write_head(self, session_id: str, snapshot: HeadSnapshot, head: SessionHead) -> HeadSnapshot:
        """Replace the head if it is still at ``snapshot.version`` (used for tombstones).

        Raises:
            StaleSessionError: The head has moved on.
        """
        await self._settle(session_id)
        value = to_wire(head)
        self._check_size(value, "session head")
        try:
            version = await self._put(session_id, HEAD_KEY, value, RESTRICTED, if_version=snapshot.version)
        except ConditionalWriteFailed:
            raise StaleSessionError("The session has been modified in storage since it was loaded.") from None
        return HeadSnapshot(head, version)

    # -- checkpoint ----------------------------------------------------------------------------------
    async def append_event(
        self,
        session_id: str,
        snapshot: HeadSnapshot,
        event: Event,
        *,
        state: dict[str, Any],
        last_update_time: float,
    ) -> HeadSnapshot:
        """Commit one event and the resulting session state as a checkpoint.

        Args:
            session_id: The session.
            snapshot: The head the caller last saw; its version is the commit precondition.
            event: The ADK event (its ``id`` must be set).
            state: The full session-scoped state after this event.
            last_update_time: The event timestamp, recorded on the head.

        Raises:
            StaleSessionError: Another writer committed first; nothing of ours is visible.
            VaultLimitExceeded: The event, head or event count is over a cap (before any request).
            InvalidState: ``state`` is not JSON-native (before any request).
        """
        await self._settle(session_id)
        if not event.id:
            raise AgentVaultError("event has no id; the runner assigns one before append")
        head = snapshot.head
        if head.event_count >= self._limits.max_events:
            raise VaultLimitExceeded(
                f"session already holds {head.event_count} events (limit {self._limits.max_events})"
            )
        seq = head.event_count + 1
        key = event_key(seq, event.id)
        record = EventRecord(seq=seq, prev_key=head.last_event_key, event=encode_event(event))
        new_head = head.model_copy(
            update={
                "revision": head.revision + 1,
                "event_count": seq,
                "last_event_key": key,
                "last_update_time": last_update_time,
                "state": state,
            }
        )
        record_value, head_value = to_wire(record), to_wire(new_head)
        self._check_size(record_value, "event")
        self._check_size(head_value, "session head")

        task = asyncio.ensure_future(
            self._commit(session_id, snapshot.version, key, event.id, record_value, new_head, head_value)
        )
        self._inflight[session_id] = task
        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                self._inflight.pop(session_id, None)

    async def _commit(
        self,
        session_id: str,
        expected_version: int,
        key: str,
        event_id: str,
        record_value: dict[str, Any],
        new_head: SessionHead,
        head_value: dict[str, Any],
    ) -> HeadSnapshot:
        try:
            await self._put(session_id, key, record_value, RESTRICTED, if_absent=True)
        except ConditionalWriteFailed:
            found = await self._get(session_id, key)
            stored_id = found[0].get("event", {}).get("id") if found and isinstance(found[0], dict) else None
            if stored_id != event_id:
                raise StaleSessionError("Another writer claimed this event slot.") from None
        try:
            version = await self._put(session_id, HEAD_KEY, head_value, RESTRICTED, if_version=expected_version)
        except ConditionalWriteFailed:
            current = await self._read_head_raw(session_id)
            if current is not None and current.head.last_event_key == key:
                return current  # our first attempt committed; its response was lost
            raise StaleSessionError("The session has been modified in storage since it was loaded.") from None
        return HeadSnapshot(new_head, version)

    # -- reads ---------------------------------------------------------------------------------------
    async def _record(self, session_id: str, key: str) -> EventRecord:
        async with self._semaphore:
            found = await self._get(session_id, key)
        if found is None:
            raise CorruptRecord("event chain is truncated: a linked event is missing")
        return decode_event_record(found[0])

    async def read_events(
        self,
        session_id: str,
        head: SessionHead,
        *,
        num_recent_events: int | None = None,
        after_timestamp: float | None = None,
    ) -> list[Event]:
        """The session's committed events, oldest first, honouring ADK's ``GetSessionConfig`` filters.

        ``num_recent_events`` and ``after_timestamp`` walk back from the head (bounded and cycle-checked);
        a full read lists the event prefix and fetches records in parallel, linking them by ``prev_key``
        so orphans from lost CAS races are ignored.

        Raises:
            CorruptRecord: The chain has a cycle, a gap, or does not match the head's ``event_count``.
        """
        await self._settle(session_id)
        if head.event_count == 0 or head.last_event_key is None:
            return []
        if num_recent_events is not None or after_timestamp is not None:
            return await self._walk(session_id, head, num_recent_events, after_timestamp)
        return await self._read_all(session_id, head)

    async def _walk(self, session_id: str, head: SessionHead, limit: int | None, after: float | None) -> list[Event]:
        events: list[Event] = []
        key: str | None = head.last_event_key
        seen: set[str] = set()
        while key is not None and (limit is None or len(events) < limit):
            if key in seen or len(seen) >= head.event_count:
                raise CorruptRecord("event chain is cyclic or longer than the head records")
            seen.add(key)
            record = await self._record(session_id, key)
            event = decode_event(record.event)
            if after is not None and event.timestamp < after:
                break
            events.append(event)
            key = record.prev_key
        events.reverse()
        return events

    async def _read_all(self, session_id: str, head: SessionHead) -> list[Event]:
        keys: list[str] = []
        page = 0
        while True:
            listing = await self._call(
                "list", self._vault.list, session_id, prefix=EVENT_PREFIX, page=page, size=_LIST_PAGE_SIZE
            )
            if not listing.entries:
                break
            keys.extend(e.key for e in listing.entries)
            page += 1
        records = dict(zip(keys, await asyncio.gather(*(self._record(session_id, k) for k in keys))))
        ordered: list[EventRecord] = []
        key: str | None = head.last_event_key
        seen: set[str] = set()
        while key is not None:
            if key in seen or len(ordered) >= head.event_count:
                raise CorruptRecord("event chain is cyclic or longer than the head records")
            seen.add(key)
            record = records.get(key)
            if record is None:
                raise CorruptRecord("event chain is truncated: a linked event is missing")
            ordered.append(record)
            key = record.prev_key
        if len(ordered) != head.event_count:
            raise CorruptRecord("event chain length does not match the head")
        return [decode_event(r.event) for r in reversed(ordered)]

    # -- scoped state ----------------------------------------------------------------------------------
    def _scoped_classification(self, scope: str) -> str:
        return self._app_cls if scope == "app" else RESTRICTED

    async def read_scoped(self, session_id: str, scope: str) -> dict[str, Any]:
        """The session's ``app`` or ``user`` state (un-prefixed keys), ``{}`` when none is stored."""
        found = await self._get(session_id, STATE_KEY, scope=scope)
        if found is None:
            return {}
        value = found[0]
        state = value.get("state") if isinstance(value, dict) else None
        if not isinstance(state, dict):
            raise CorruptRecord(f"stored {scope} state is malformed")
        return state

    async def merge_scoped(self, session_id: str, scope: str, delta: dict[str, Any]) -> None:
        """Merge ``delta`` into the ``app``/``user`` state, per key (last writer wins on a key).

        Concurrent sessions of the same user contend on one record, so a lost precondition is retried
        from a fresh read rather than surfaced as a stale session.
        """
        if not delta:
            return
        for _ in range(_SCOPED_MERGE_ATTEMPTS):
            found = await self._get(session_id, STATE_KEY, scope=scope)
            current: dict[str, Any] = {}
            if found is not None and isinstance(found[0], dict) and isinstance(found[0].get("state"), dict):
                current = found[0]["state"]
            value = {"schema": 1, "state": {**current, **delta}}
            self._check_size(value, f"{scope} state")
            conditions: dict[str, Any] = {"if_version": found[1]} if found is not None else {"if_absent": True}
            try:
                await self._put(
                    session_id, STATE_KEY, value, self._scoped_classification(scope), scope=scope, **conditions
                )
                return
            except ConditionalWriteFailed:
                continue
        raise AgentVaultError(f"{scope} state stayed contended after {_SCOPED_MERGE_ATTEMPTS} attempts")

    # -- failure record ------------------------------------------------------------------------------
    async def write_failure(self, session_id: str, record: FailureRecord) -> None:
        """Write the failure summary once; an existing record is kept (the write is idempotent)."""
        try:
            await self._put(session_id, FAILURE_KEY, to_wire(record), RESTRICTED, if_absent=True)
        except ConditionalWriteFailed:
            return

    async def read_failure(self, session_id: str) -> FailureRecord | None:
        found = await self._get(session_id, FAILURE_KEY)
        return decode_failure(found[0]) if found is not None else None
