"""``GtsSessionService``: an ADK 2 ``BaseSessionService`` persisted in the GTS session vault.

Swap it in for ``InMemorySessionService`` with configuration only::

    service = GtsSessionService.from_client(client, organisation_id=2, template_name="adk-agent")
    runner = Runner(app=app, session_service=service)

or, through ADK's service registry, ``--session_service_uri "gts://host:port?org=2&template=adk-agent"``
after :func:`register_gts_session_service` (credentials come from ``AGENCY_*`` environment variables).

One ADK session is one GTS session: the ADK ``session_id`` *is* the GTS session id. A dispatched agent
adopts the id it was given; ``create_session`` without an id registers a new GTS session from the
configured template. Every non-partial event handed to :meth:`append_event` is a **checkpoint**,
committed by a conditional write on the session head before the runner moves on, and a writer whose
copy of the session is out of date gets ADK's ``StaleSessionError`` having changed nothing.

``app:`` and ``user:`` state live in the session vault's ``app``/``user`` scopes, which comand resolves
from the session itself, so this service never names an app or a user for them. ``temp:`` state is
never persisted. ``get_user_state`` (no session in hand) is deliberately not supported.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from google.adk.errors import StaleSessionError
from google.adk.errors.already_exists_error import AlreadyExistsError
from google.adk.events import Event
from google.adk.sessions import BaseSessionService, Session
from google.adk.sessions._session_util import extract_state_delta
from google.adk.sessions.base_session_service import GetSessionConfig, ListSessionsResponse
from google.adk.sessions.state import State

from agency_sdk.adk._records import SessionHead
from agency_sdk.adk._store import HeadSnapshot, VaultLimits, VaultStore
from agency_sdk.delegates.session_dto import SessionStatus

_SCOPES = (("app", State.APP_PREFIX), ("user", State.USER_PREFIX))
_SEARCH_PAGE = 100


def _session_only(state: dict[str, Any]) -> dict[str, Any]:
    prefixes = (State.APP_PREFIX, State.USER_PREFIX, State.TEMP_PREFIX)
    return {k: v for k, v in state.items() if not k.startswith(prefixes)}


class GtsSessionService(BaseSessionService):
    """ADK session service backed by the GTS session vault (see the module docstring)."""

    def __init__(
        self,
        *,
        vault: Any,
        sessions: Any,
        templates: Any,
        organisation_id: int,
        template_name: str | None = None,
        bind_owner: bool = True,
        base_url: str | None = None,
        timeout: float = 10.0,
        limits: VaultLimits | None = None,
        app_state_classification: str = "restricted",
    ) -> None:
        """
        Args:
            vault: The session-vault delegate (``AgencyClient.session_vault()``).
            sessions: The session delegate (``AgencyClient.sessions()``).
            templates: The session-template delegate (``AgencyClient.session_templates()``).
            organisation_id: The organisation every session belongs to.
            template_name: Template used when ``create_session`` gets no id; without one, such calls fail.
            bind_owner: Send the ADK ``user_id`` as the session owner claim when registering.
            base_url: The control-plane URL, for information only.
            timeout: Per-request timeout in seconds.
            limits: Client-side size and event-count caps.
            app_state_classification: ``restricted`` (default) or, by explicit opt-in, ``internal``.
        """
        self.organisation_id = organisation_id
        self.template_name = template_name
        self.base_url = base_url
        self._bind_owner = bind_owner
        self._sessions = sessions
        self._templates = templates
        self._store = VaultStore(
            vault,
            organisation_id,
            timeout=timeout,
            limits=limits,
            app_state_classification=app_state_classification,
        )
        self._heads: dict[str, HeadSnapshot] = {}

    @property
    def sessions(self) -> Any:
        """The session delegate (status updates, correlation lookup) this service writes through."""
        return self._sessions

    @property
    def store(self) -> VaultStore:
        """The vault seam (for ``DurableRunner``'s failure record)."""
        return self._store

    @classmethod
    def from_client(
        cls, client: Any, *, organisation_id: int, template_name: str | None = None, **kwargs: Any
    ) -> "GtsSessionService":
        """Build the service from an ``AgencyClient``, sharing its credentials and base URL."""
        return cls(
            vault=client.session_vault(),
            sessions=client.sessions(),
            templates=client.session_templates(),
            organisation_id=organisation_id,
            template_name=template_name,
            base_url=client.base_url,
            **kwargs,
        )

    # -- helpers -----------------------------------------------------------------------------------------
    def _remember(self, session_id: str, snapshot: HeadSnapshot) -> None:
        self._heads[session_id] = snapshot

    async def _scoped_state(self, session_id: str) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for scope, prefix in _SCOPES:
            for key, value in (await self._store.read_scoped(session_id, scope)).items():
                merged[prefix + key] = value
        return merged

    async def _merge_scopes(self, session_id: str, deltas: dict[str, dict[str, Any]]) -> None:
        for scope, _ in _SCOPES:
            if deltas[scope]:
                await self._store.merge_scoped(session_id, scope, deltas[scope])

    def _to_session(
        self, session_id: str, snapshot: HeadSnapshot, events: list[Event], scoped: dict[str, Any]
    ) -> Session:
        head = snapshot.head
        session = Session(
            id=session_id,
            app_name=head.app_name,
            user_id=head.user_id,
            state={**head.state, **scoped},
            events=events,
            last_update_time=head.last_update_time,
        )
        session._storage_update_marker = str(snapshot.version)
        self._remember(session_id, snapshot)
        return session

    async def _visible_head(self, app_name: str, user_id: str | None, session_id: str) -> HeadSnapshot | None:
        snapshot = await self._store.read_head(session_id)
        if snapshot is None or snapshot.head.deleted or snapshot.head.app_name != app_name:
            return None
        if user_id is not None and snapshot.head.user_id != user_id:
            return None
        return snapshot

    # -- BaseSessionService ------------------------------------------------------------------------------
    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
        correlation_id: str | None = None,
    ) -> Session:
        """Create a session: register a new GTS session from the template, or adopt ``session_id``.

        ``correlation_id`` (an extension to ADK's signature, used by ``DurableRunner``) labels a newly
        registered session; it defaults to a random id.

        Raises:
            AlreadyExistsError: ``session_id`` already has an ADK session.
            ValueError: No ``session_id`` and no ``template_name`` configured.
            SessionTemplateNotFound: The configured template does not exist.
            VaultUnavailable: comand refused a scope (for example no bound owner); nothing was written.
        """
        deltas = extract_state_delta(state or {})
        if session_id is None:
            if not self.template_name:
                raise ValueError("create_session without a session_id needs template_name to be configured")
            registered: str = await asyncio.to_thread(
                self._templates.register,
                self.organisation_id,
                self.template_name,
                correlation_id=correlation_id or uuid.uuid4().hex,
                labels={"adk_app": app_name},
                owner=user_id if self._bind_owner else None,
            )
            sid = registered
        else:
            sid = session_id
            if await self._store.read_head(sid) is not None:
                raise AlreadyExistsError(f"Session {sid} already exists.")
            await asyncio.to_thread(
                self._sessions.update,
                self.organisation_id,
                status=SessionStatus.IN_PROGRESS,
                labels={"adk_app": app_name, **({"correlation_id": correlation_id} if correlation_id else {})},
                session_id=sid,
            )
        await self._scoped_state(sid)  # fail closed before writing anything if a scope is refused
        head = SessionHead(
            app_name=app_name,
            user_id=user_id,
            correlation_id=correlation_id,
            last_update_time=time.time(),
            state=deltas["session"],
        )
        snapshot = await self._store.create_head(sid, head)
        await self._merge_scopes(sid, deltas)
        return self._to_session(sid, snapshot, [], await self._scoped_state(sid))

    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        config: Optional[GetSessionConfig] = None,
    ) -> Optional[Session]:
        """Read a session back: head, events (honouring ``config``) and merged ``app:``/``user:`` state.

        Never writes. Returns ``None`` for an unknown or deleted session, or one owned by another app/user.
        """
        snapshot = await self._visible_head(app_name, user_id, session_id)
        if snapshot is None:
            return None
        events = await self._store.read_events(
            session_id,
            snapshot.head,
            num_recent_events=config.num_recent_events if config else None,
            after_timestamp=config.after_timestamp if config else None,
        )
        return self._to_session(session_id, snapshot, events, await self._scoped_state(session_id))

    async def list_sessions(self, *, app_name: str, user_id: Optional[str] = None) -> ListSessionsResponse:
        """List the app's sessions (optionally one user's), oldest-updated first, without events or state."""
        found: list[Session] = []
        page = 0
        while True:
            result = await asyncio.to_thread(
                self._sessions.search,
                self.organisation_id,
                labels={"adk_app": app_name},
                user=user_id,
                page=page,
                size=_SEARCH_PAGE,
            )
            for ref in result.items:
                snapshot = await self._visible_head(app_name, user_id, ref.session_id)
                if snapshot is not None:
                    found.append(
                        Session(
                            id=ref.session_id,
                            app_name=app_name,
                            user_id=snapshot.head.user_id,
                            last_update_time=snapshot.head.last_update_time,
                        )
                    )
            if not result.items or (page + 1) * result.page.size >= result.page.total:
                break
            page += 1
        found.sort(key=lambda s: s.last_update_time)
        return ListSessionsResponse(sessions=found)

    async def delete_session(self, *, app_name: str, user_id: str, session_id: str) -> None:
        """Tombstone the session: reads return ``None``, history stays for forensics. Idempotent."""
        for _ in range(3):
            snapshot = await self._visible_head(app_name, user_id, session_id)
            if snapshot is None:
                return
            try:
                await self._store.write_head(session_id, snapshot, snapshot.head.model_copy(update={"deleted": True}))
                self._heads.pop(session_id, None)
                return
            except StaleSessionError:
                continue
        raise StaleSessionError(f"Session {session_id} kept changing while being deleted.")

    async def append_event(self, session: Session, event: Event) -> Event:
        """Commit ``event`` as a checkpoint, then apply it to the in-memory ``session``.

        Order: trim ``temp:``; write the event; conditionally write the head (the commit); only then
        mutate ``session`` and its storage marker; finally merge ``app:``/``user:`` deltas. A stale copy
        raises ``StaleSessionError`` before anything is mutated or merged.
        """
        if event.partial:
            return event
        self._apply_temp_state(session, event)
        event = self._trim_temp_delta_state(event)
        if not event.id:
            event.id = Event.new_id()
        snapshot = await self._snapshot_for(session)
        deltas = extract_state_delta(event.actions.state_delta if event.actions else {})
        session_state = {**_session_only(session.state), **deltas["session"]}
        committed = await self._store.append_event(
            session.id, snapshot, event, state=session_state, last_update_time=event.timestamp
        )
        self._remember(session.id, committed)
        self._commit_event_to_session(session, event)
        session._storage_update_marker = str(committed.version)
        session.last_update_time = event.timestamp
        await self._merge_scopes(session.id, deltas)
        return event

    async def _snapshot_for(self, session: Session) -> HeadSnapshot:
        cached = self._heads.get(session.id)
        marker = session._storage_update_marker
        if cached is not None and marker is not None and str(cached.version) == marker:
            return cached
        fresh = await self._store.read_head(session.id)
        if fresh is None or fresh.head.deleted:
            raise ValueError(f"Session {session.id} not found.")
        if marker is not None and str(fresh.version) != marker:
            raise StaleSessionError("The session has been modified in storage since it was loaded.")
        self._remember(session.id, fresh)
        return fresh

    # -- recovery ----------------------------------------------------------------------------------------
    async def repair_scoped_state(self, session: Session) -> None:
        """Re-apply the last committed event's ``app:``/``user:`` deltas (a write path, run at resume).

        Scoped merges happen after the head commit, so a crash in between loses them; replaying the
        last event's delta is idempotent per key (last writer wins on a key — an accepted limit).
        """
        snapshot = await self._store.read_head(session.id)
        if snapshot is None:
            return
        last = await self._store.read_events(session.id, snapshot.head, num_recent_events=1)
        if not last or not last[0].actions:
            return
        deltas = extract_state_delta(last[0].actions.state_delta)
        await self._merge_scopes(session.id, deltas)
        session.state.update(await self._scoped_state(session.id))


def register_gts_session_service(scheme: str = "gts") -> None:
    """Register ``gts://host[:port]?org=<id>&template=<name>[&tls=0]`` with ADK's service registry.

    Credentials come from ``AGENCY_AUTH_URL``, ``AGENCY_CLIENT_ID`` and ``AGENCY_CLIENT_SECRET``. ``tls=0``
    selects plain HTTP (local development); the default is HTTPS.
    """
    from google.adk.cli.service_registry import get_service_registry

    get_service_registry().register_session_service(scheme, _service_from_uri)


def _service_from_uri(uri: str, **_: Any) -> GtsSessionService:
    from agency_sdk.client import AgencyClient
    from agency_sdk.credentials import CredentialsSupplier

    parsed = urlparse(uri)
    query = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
    if "org" not in query:
        raise ValueError("gts:// session service URI needs ?org=<organisation id>")
    missing = [n for n in ("AGENCY_AUTH_URL", "AGENCY_CLIENT_ID", "AGENCY_CLIENT_SECRET") if not os.environ.get(n)]
    if missing:
        raise ValueError(f"gts:// session service needs environment variables: {', '.join(missing)}")
    scheme = "http" if query.get("tls") == "0" else "https"
    credentials = CredentialsSupplier(
        auth_base_url=os.environ["AGENCY_AUTH_URL"],
        client_id=os.environ["AGENCY_CLIENT_ID"],
        client_secret=os.environ["AGENCY_CLIENT_SECRET"],
    )
    client = AgencyClient(token_supplier=credentials, base_url=f"{scheme}://{parsed.netloc}")
    return GtsSessionService.from_client(client, organisation_id=int(query["org"]), template_name=query.get("template"))
