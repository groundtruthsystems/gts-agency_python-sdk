"""In-process (optionally file-backed) fakes of comand for the Agent Vault tests — no network.

``FakeVault`` is a duck-typed ``AgencySessionVaultClient`` implementing the backend contract in
``docs/agent_vault_gap_analysis.md`` §4: per-key monotonic ``version``, ``if_version`` (412) /
``if_absent`` (409) raised as ``ConditionalWriteFailed``, lexicographic ``prefix`` listing with
``page``/``size``, and ``scope=app|user`` resolved *server-side*. Every call is appended to ``log``
and ``peak_inflight`` records the highest number of concurrent calls.

``FakeControlPlane`` adds the session and session-template endpoints as comand implements them
(``documentation/AGENT_VAULT_API.md``): ``_search`` filters, ``register`` (correlation from
``input.correlation``, else the session id; owner = the caller), ``resolve_session`` (live first, else
newest; only the template agent may call it; one session per correlation under concurrent creates) and
status updates. Correlation ids written to a session must pass comand's write rule. It owns a
``FakeVault`` whose ``app``/``user`` scopes resolve the way comand does: ``user`` to the session's owner
(403 when none — fail closed) and ``app`` to the session's template.

Pass ``path=`` to persist everything to a JSON file under an exclusive ``flock``, so separate
processes (the cross-process read-back and SIGKILL scenarios) share one fake backend.

``FakeGraphs`` (``cp.graphs``) is a duck-typed ``AgencyGraphsClient``: seed it with ``add_graph``. Set
``error`` to make every call raise it (e.g. ``http_error(500)``).

Hooks for failure injection: ``FakeVault.on_set(key, apply)`` runs instead of a plain write (call
``apply()`` to commit); ``FakeVault.get_delay`` makes every ``get`` sleep.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

import requests

from agency_sdk.delegates.datasets_dto import Page
from agency_sdk.delegates.graphs_dto import KnowledgeGraphEntry, KnowledgeGraphList
from agency_sdk.delegates.session_dto import SessionCommandResponse, SessionSearchResult, SessionSummary
from agency_sdk.delegates.session_hitl_client import HitlAlreadyAnswered, HitlNotAuthorised, HitlRequestExpired
from agency_sdk.delegates.session_hitl_dto import HitlRequest
from agency_sdk.delegates.session_templates_client import (
    CorrelationMismatch,
    SessionAlreadyExists,
    SessionTemplateNotFound,
)
from agency_sdk.delegates.session_templates_dto import RegisteredSession, ResolvedSession
from agency_sdk.delegates.session_vault_client import ConditionalWriteFailed
from agency_sdk.delegates.session_vault_dto import (
    VaultEntryMeta,
    VaultEntryResponse,
    VaultListResponse,
    VaultSetResponse,
)
from agency_sdk.domain import Page as DomainPage  # graphs use the shared Page; session fakes keep datasets_dto's
from agency_sdk.test.conftest import StubResponse


def http_error(status: int, body: Any = None) -> requests.HTTPError:
    return requests.HTTPError(f"{status} error", response=StubResponse(json_data=body, status_code=status))


_CORRELATION_WRITE = re.compile(r"[A-Za-z0-9._:-]{10,128}")
_SSN = re.compile(r"\d{3}-?\d{2}-?\d{4}")
#: The fake's default caller and the template agent's service account (``resolve_session`` answers only it).
AGENT_PRINCIPAL = "sa:agent"
_LIVE = (1, 2, 3)


def check_correlation_write(value: str) -> None:
    """comand's write rule (``agent_vault/ids.rs``): 400 for anything else."""
    if not _CORRELATION_WRITE.fullmatch(value) or _SSN.fullmatch(value):
        raise http_error(400, {"error": {"type": "BAD_REQUEST", "message": "Invalid correlation_id"}})


@dataclass
class _Entry:
    value: Any
    classification: str
    version: int


@dataclass
class Call:
    op: str
    session_id: str
    key: str | None
    scope: str | None
    kwargs: dict[str, Any] = field(default_factory=dict)


class _Shared:
    """State shared by the vault and control-plane fakes; optionally persisted to a file."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self.lock = threading.RLock()
        self.vault: dict[tuple[str, str | None, str], _Entry] = {}
        self.sessions: dict[str, dict[str, Any]] = {}
        self.templates: dict[str, str] = {"adk-agent": "tpl-adk"}
        self.seq = 0
        self.hitl: dict[str, dict[str, dict[str, Any]]] = {}
        if self.path is not None and self.path.exists():
            with self.io():
                pass

    @contextlib.contextmanager
    def io(self) -> Iterator[None]:
        with self.lock:
            if self.path is None:
                yield
                return
            lock_path = self.path.with_suffix(".lock")
            with open(lock_path, "a+") as lock_file:
                fcntl.flock(lock_file, fcntl.LOCK_EX)
                try:
                    self._load()
                    yield
                    self._save()
                finally:
                    fcntl.flock(lock_file, fcntl.LOCK_UN)

    def _load(self) -> None:
        assert self.path is not None
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text() or "{}")
        self.vault = {
            (e["space"], e["scope"], e["key"]): _Entry(e["value"], e["classification"], e["version"])
            for e in raw.get("vault", [])
        }
        self.sessions = raw.get("sessions", {})
        self.templates = raw.get("templates", self.templates)
        self.seq = raw.get("seq", 0)
        self.hitl = raw.get("hitl", {})

    def _save(self) -> None:
        assert self.path is not None
        raw = {
            "vault": [
                {
                    "space": s,
                    "scope": sc,
                    "key": k,
                    "value": e.value,
                    "classification": e.classification,
                    "version": e.version,
                }
                for (s, sc, k), e in self.vault.items()
            ],
            "sessions": self.sessions,
            "templates": self.templates,
            "seq": self.seq,
            "hitl": self.hitl,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(raw))
        tmp.replace(self.path)


class FakeVault:
    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        shared: _Shared | None = None,
        scope_space: Callable[[str, str], str | None] | None = None,
    ) -> None:
        self._shared = shared or _Shared(path)
        #: (session_id, scope) -> storage space; ``None`` means the server refuses (fail closed).
        self._scope_space = scope_space or (lambda session_id, scope: session_id)
        self.log: list[Call] = []
        self.on_set: Callable[[str, Callable[[], VaultSetResponse]], VaultSetResponse] | None = None
        self.get_delay = 0.0
        self.inflight = 0
        self.peak_inflight = 0
        self._counter = threading.Lock()

    @property
    def data(self) -> dict[tuple[str, str | None, str], _Entry]:
        return self._shared.vault

    # -- helpers ---------------------------------------------------------------------------------------
    def _enter(self, call: Call) -> None:
        with self._counter:
            self.log.append(call)
            self.inflight += 1
            self.peak_inflight = max(self.peak_inflight, self.inflight)

    def _exit(self) -> None:
        with self._counter:
            self.inflight -= 1

    def _space(self, session_id: str, scope: str | None) -> str:
        if scope is None:
            return session_id
        space = self._scope_space(session_id, scope)
        if space is None:
            raise http_error(403, {"error": {"type": "FORBIDDEN", "message": "no bound owner"}})
        return space

    def keys(self, session_id: str, scope: str | None = None) -> list[str]:
        with self._shared.io():
            space = self._space(session_id, scope) if scope else session_id
            return sorted(k for (s, sc, k) in self.data if s == space and sc == scope)

    def value(self, session_id: str, key: str, scope: str | None = None) -> Any:
        with self._shared.io():
            return self.data[(self._space(session_id, scope) if scope else session_id, scope, key)].value

    def writes(self) -> list[Call]:
        return [c for c in self.log if c.op in ("set", "delete")]

    # -- AgencySessionVaultClient surface ----------------------------------------------------------------
    def get(
        self,
        organisation_id: int,
        session_id: str,
        key: str,
        reveal: bool = False,
        *,
        scope: str | None = None,
        timeout: float | None = None,
    ) -> VaultEntryResponse:
        self._enter(Call("get", session_id, key, scope, {"timeout": timeout, "reveal": reveal}))
        try:
            if self.get_delay:
                time.sleep(self.get_delay)
            with self._shared.io():
                entry = self.data.get((self._space(session_id, scope), scope, key))
                if entry is None:
                    raise http_error(404)
                return VaultEntryResponse(
                    key=key, value=entry.value, classification=entry.classification, version=entry.version
                )
        finally:
            self._exit()

    def set(
        self,
        organisation_id: int,
        session_id: str,
        key: str,
        value: Any,
        classification: str | None = None,
        *,
        if_version: int | None = None,
        if_absent: bool = False,
        scope: str | None = None,
        timeout: float | None = None,
    ) -> VaultSetResponse:
        self._enter(
            Call(
                "set",
                session_id,
                key,
                scope,
                {
                    "classification": classification,
                    "if_version": if_version,
                    "if_absent": if_absent,
                    "timeout": timeout,
                },
            )
        )
        try:

            def apply() -> VaultSetResponse:
                with self._shared.io():
                    slot = (self._space(session_id, scope), scope, key)
                    current = self.data.get(slot)
                    if if_absent and current is not None:
                        raise ConditionalWriteFailed("exists", response=StubResponse(status_code=409))
                    if if_version is not None and (current is None or current.version != if_version):
                        raise ConditionalWriteFailed("version_mismatch", response=StubResponse(status_code=412))
                    version = (current.version if current else 0) + 1
                    self.data[slot] = _Entry(json.loads(json.dumps(value)), classification or "restricted", version)
                    return VaultSetResponse(key=key, classification=classification or "restricted", version=version)

            if self.on_set is not None:
                return self.on_set(key, apply)
            return apply()
        finally:
            self._exit()

    def list(
        self,
        organisation_id: int,
        session_id: str,
        *,
        prefix: str | None = None,
        page: int | None = None,
        size: int | None = None,
        scope: str | None = None,
        timeout: float | None = None,
    ) -> VaultListResponse:
        self._enter(Call("list", session_id, prefix, scope, {"page": page, "size": size, "timeout": timeout}))
        try:
            with self._shared.io():
                space = self._space(session_id, scope)
                keys = sorted(
                    k
                    for (s, sc, k) in self.data
                    if s == space and sc == scope and (prefix is None or k.startswith(prefix))
                )
                if size is not None:
                    start = (page or 0) * size
                    keys = keys[start : start + size]
                return VaultListResponse(
                    entries=[
                        VaultEntryMeta(
                            key=k,
                            size=len(json.dumps(self.data[(space, scope, k)].value)),
                            updated_at="t",
                            classification=self.data[(space, scope, k)].classification,
                            version=self.data[(space, scope, k)].version,
                        )
                        for k in keys
                    ]
                )
        finally:
            self._exit()

    def delete(
        self,
        organisation_id: int,
        session_id: str,
        key: str,
        *,
        scope: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self._enter(Call("delete", session_id, key, scope, {"timeout": timeout}))
        try:
            with self._shared.io():
                self.data.pop((self._space(session_id, scope), scope, key), None)
        finally:
            self._exit()


class FakeControlPlane:
    """comand's session, template (and later HITL) endpoints plus a scope-resolving ``FakeVault``."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.shared = _Shared(path)
        self.vault = FakeVault(shared=self.shared, scope_space=self._scope_space)
        self.sessions = _FakeSessions(self)
        self.templates = _FakeTemplates(self)
        self.hitl = _FakeHitl(self)
        self.graphs = FakeGraphs()
        self.log: list[tuple[str, dict[str, Any]]] = []

    def _scope_space(self, session_id: str, scope: str) -> str | None:
        session = self.shared.sessions.get(session_id)
        if session is None:
            return None
        if scope == "user":
            owner = session.get("owner")
            return f"user:{owner}" if owner else None
        template = session.get("template")
        return f"app:{template}" if template else None

    def add_session(
        self,
        session_id: str,
        *,
        owner: str | None = None,
        correlation_id: str | None = None,
        template: str | None = "adk-agent",
        status: int = 2,
    ) -> None:
        """Seed a dispatched session (as comand's execute path would have created it)."""
        with self.shared.io():
            self.shared.sessions[session_id] = self._new_record(
                session_id, template=template, correlation_id=correlation_id, owner=owner, status=status
            )

    def _new_record(
        self, session_id: str, *, template: str | None, correlation_id: str | None, owner: str | None, status: int
    ) -> dict[str, Any]:
        self.shared.seq += 1
        return {
            "status": status,
            "template": template,
            "correlation_id": correlation_id or session_id,
            "owner": owner,
            "created": self.shared.seq,
        }

    def expire(self, session_id: str, request_id: str) -> None:
        """Model comand's background job: expire a pending request and fail its session (server time)."""
        with self.shared.io():
            self.shared.hitl[session_id][request_id]["status"] = "expired"
            self.shared.sessions[session_id]["status"] = -1

    def session(self, session_id: str) -> dict[str, Any]:
        with self.shared.io():
            return json.loads(json.dumps(self.shared.sessions[session_id]))

    def sessions_of(self, correlation_id: str) -> list[str]:
        """The ids of every session of a task, oldest first."""
        with self.shared.io():
            found = [
                (s["created"], sid) for sid, s in self.shared.sessions.items() if s["correlation_id"] == correlation_id
            ]
        return [sid for _, sid in sorted(found)]


class _FakeSessions:
    def __init__(self, cp: FakeControlPlane) -> None:
        self._cp = cp

    def update(
        self,
        organisation_id: int,
        *,
        status: int,
        result: dict[str, Any] | None = None,
        events: list[dict[str, Any]] | None = None,
        metrics: dict[str, Any] | None = None,
        error: str | None = None,
        logs: str | None = None,
        session_id: str | None = None,
    ) -> SessionCommandResponse:
        self._cp.log.append(("update", {"session_id": session_id, "status": int(status), "result": result}))
        with self._cp.shared.io():
            session = self._cp.shared.sessions.get(session_id or "")
            if session is None:
                raise http_error(404)
            terminal = session["status"] in (-1, 0)
            if not (terminal and int(status) not in (-1, 0)):  # comand's stale-regression guard
                session["status"] = int(status)
            if result is not None:
                session["result"] = result
        return SessionCommandResponse(success=True, message="ok")

    def get(self, session_id: str) -> SessionSummary:
        self._cp.log.append(("get", {"session_id": session_id}))
        with self._cp.shared.io():
            s = self._cp.shared.sessions.get(session_id)
            if s is None:
                raise http_error(404)
            return SessionSummary(
                id=session_id,
                type="agent",
                status=s["status"],
                organisation=2,
                version=1,
                created_on="2026-11-19T00:00:00Z",
                correlation_id=s["correlation_id"],
                user_id=s["owner"] or "",
                template_name=s["template"],
            )

    def search(
        self,
        organisation_id: int,
        *,
        correlation_id: str | None = None,
        user_id: str | None = None,
        template_id: str | None = None,
        status: list[int] | None = None,
        page: int = 0,
        size: int = 50,
        sort: tuple[str, str] | None = None,
    ) -> SessionSearchResult:
        self._cp.log.append(
            ("search", {"correlation_id": correlation_id, "user_id": user_id, "template_id": template_id})
        )
        with self._cp.shared.io():
            templates = self._cp.shared.templates
            rows = sorted(self._cp.shared.sessions.items(), key=lambda kv: kv[1]["created"], reverse=True)
            matches = [
                SessionSummary(
                    id=sid,
                    type="agent",
                    status=s["status"],
                    organisation=organisation_id,
                    version=1,
                    created_on=f"2026-11-19T00:00:{s['created'] % 60:02d}Z",
                    correlation_id=s["correlation_id"],
                    user_id=s["owner"] or "",
                    template_id=templates.get(s["template"]) if s["template"] else None,
                    template_name=s["template"],
                )
                for sid, s in rows
                if (correlation_id is None or s["correlation_id"] == correlation_id)
                and (user_id is None or s["owner"] == user_id)
                and (template_id is None or (s["template"] and templates.get(s["template"]) == template_id))
                and (status is None or s["status"] in [int(x) for x in status])
            ]
        items = matches[page * size : (page + 1) * size]
        return SessionSearchResult(page=Page(total=len(matches), page=page, size=size), items=items)


class _FakeTemplates:
    def __init__(self, cp: FakeControlPlane) -> None:
        self._cp = cp
        #: The principal calling (``register`` owner; ``resolve_session`` answers only ``AGENT_PRINCIPAL``).
        self.caller = AGENT_PRINCIPAL
        #: comand's fixed behaviour: the first resolve that finds the dispatched session itself (it already
        #: carries the correlation id) does the first vault commit and reports ``created=True``. ``False`` models
        #: the earlier server, which reported ``created=False`` and skipped that commit.
        self.dispatched_match_reports_created = True

    def resolve_id(self, organisation_id: int, name: str) -> str:
        with self._cp.shared.io():
            if name not in self._cp.shared.templates:
                raise SessionTemplateNotFound(f"No session template named {name!r}")
            return self._cp.shared.templates[name]

    def register(
        self,
        organisation_id: int,
        template_name: str,
        *,
        correlation_id: str | None = None,
        input: dict[str, Any] | None = None,
    ) -> RegisteredSession:
        self._cp.log.append(("register", {"template": template_name, "correlation_id": correlation_id}))
        if correlation_id is not None:
            check_correlation_write(correlation_id)
        with self._cp.shared.io():
            if template_name not in self._cp.shared.templates:
                raise SessionTemplateNotFound(f"No session template named {template_name!r}")
            sid = f"s-{uuid.uuid4().hex[:12]}"
            record = self._cp._new_record(
                sid, template=template_name, correlation_id=correlation_id, owner=self.caller, status=2
            )
            self._cp.shared.sessions[sid] = record
            return RegisteredSession(session_id=sid, correlation_id=record["correlation_id"], user_id=self.caller)

    def resolve_session(
        self,
        organisation_id: int,
        template_name: str,
        *,
        mode: str,
        correlation_id: str,
        session_id: str | None = None,
        input: dict[str, Any] | None = None,
        state: dict[str, Any] | None = None,
        framework_user_id: str | None = None,
    ) -> ResolvedSession | None:
        self._cp.log.append(("resolve", {"mode": mode, "session_id": session_id}))
        if mode != "find":
            check_correlation_write(correlation_id)
        with self._cp.shared.io():
            if template_name not in self._cp.shared.templates:
                raise SessionTemplateNotFound(f"No session template named {template_name!r}")
            if self.caller != AGENT_PRINCIPAL:  # only the template agent may resolve: a plain 404
                if mode == "find":
                    return None
                raise http_error(404)
            sessions = self._cp.shared.sessions
            mine = [
                (sid, s)
                for sid, s in sessions.items()
                if s["template"] == template_name and s["correlation_id"] == correlation_id
            ]
            mine.sort(key=lambda kv: (kv[1]["status"] in _LIVE, kv[1]["created"]), reverse=True)
            if mine:  # comand matches the correlation id first, even when a session_id is given
                if mode == "create":
                    raise SessionAlreadyExists("A session already exists for this correlation id")
                sid, s = mine[0]
                first = sid == session_id and not s.get("vault_committed")
                if first and self.dispatched_match_reports_created:
                    s["vault_committed"] = True
                    return ResolvedSession(session_id=sid, created=True, status=s["status"], vault_version=1)
                version = 1 if s.get("vault_committed") else 0
                return ResolvedSession(session_id=sid, created=False, status=s["status"], vault_version=version)
            if mode == "find":
                return None
            if session_id is not None:  # attach to the dispatched session
                target = sessions.get(session_id)
                if target is None:
                    raise http_error(404)
                if target["status"] in (0, -1, -2):
                    raise http_error(409, {"error": {"type": "CONFLICT", "details": {"code": "session_terminal"}}})
                if target["correlation_id"] not in (session_id, correlation_id):
                    raise CorrelationMismatch("The session already carries a different correlation id")
                target["correlation_id"] = correlation_id
                target["vault_committed"] = True
                return ResolvedSession(session_id=session_id, created=True, status=target["status"], vault_version=1)
            sid = f"s-{uuid.uuid4().hex[:12]}"
            record = self._cp._new_record(
                sid, template=template_name, correlation_id=correlation_id, owner=self.caller, status=1
            )
            record["vault_committed"] = True
            sessions[sid] = record
            return ResolvedSession(session_id=sid, created=True, status=1, vault_version=1)


class _FakeHitl:
    """comand's HITL resource: agent creates/reads; a human principal answers once, before expiry."""

    def __init__(self, cp: FakeControlPlane) -> None:
        self._cp = cp

    def _get(self, session_id: str, request_id: str) -> dict[str, Any]:
        record = self._cp.shared.hitl.get(session_id, {}).get(request_id)
        if record is None:
            raise http_error(404)
        return record

    def create(
        self,
        organisation_id: int,
        session_id: str,
        *,
        request_id: str,
        invocation_id: str,
        prompt: dict[str, Any],
        expires_at: str,
    ) -> HitlRequest:
        self._cp.log.append(("hitl.create", {"session_id": session_id, "request_id": request_id}))
        with self._cp.shared.io():
            if session_id not in self._cp.shared.sessions:
                raise http_error(404)
            bucket = self._cp.shared.hitl.setdefault(session_id, {})
            if request_id not in bucket:  # idempotent on request_id
                bucket[request_id] = {
                    "request_id": request_id,
                    "invocation_id": invocation_id,
                    "status": "pending",
                    "prompt": prompt,
                    "expires_at": expires_at,
                    "created_at": "2026-10-12T00:00:00Z",
                }
            self._cp.shared.sessions[session_id]["status"] = 3  # atomically AWAITING_INPUT
            return HitlRequest(**bucket[request_id])

    def get(self, organisation_id: int, session_id: str, request_id: str) -> HitlRequest:
        with self._cp.shared.io():
            return HitlRequest(**self._get(session_id, request_id))

    def list(self, organisation_id: int, session_id: str, *, status: str | None = None) -> list[HitlRequest]:
        self._cp.log.append(("hitl.list", {"session_id": session_id}))
        with self._cp.shared.io():
            return [
                HitlRequest(**r)
                for r in self._cp.shared.hitl.get(session_id, {}).values()
                if status is None or r["status"] == status
            ]

    def respond(
        self,
        organisation_id: int,
        session_id: str,
        request_id: str,
        response: Any,
        *,
        principal: str = "human:reviewer-1",
    ) -> HitlRequest:
        self._cp.log.append(("hitl.respond", {"session_id": session_id, "request_id": request_id}))
        with self._cp.shared.io():
            record = self._get(session_id, request_id)
            if principal.startswith("service:"):
                raise HitlNotAuthorised("service principals cannot answer", response=StubResponse(status_code=403))
            if record["status"] == "expired":
                raise HitlRequestExpired("expired", response=StubResponse(status_code=410))
            if record["status"] == "answered":
                raise HitlAlreadyAnswered("already answered", response=StubResponse(status_code=409))
            record.update(
                status="answered",
                response=response,
                responder=principal.split(":", 1)[1],
                answered_at="2026-10-12T01:00:00Z",
            )
            return HitlRequest(**record)


# comand's answer to a negative or overflowing page request (gts-agency ``page_entries``).
_I32_MAX = 2**31 - 1
_INVALID_PAGE_BODY = {"error": {"type": "SERVICE_ERROR", "req_uuid": "req-fake", "message": "Invalid page request"}}


class FakeGraphs:
    """In-memory ``AgencyGraphsClient``: the knowledge-graph catalogue, paged as comand pages it.

    Sorted by ``code``, 0-based, defaults ``page=0`` / ``size=10``, offset ``page * size``. A page past the end
    or ``size=0`` is an empty page with the real ``total``; a negative ``page``/``size`` raises the 500
    ``SERVICE_ERROR`` ``HTTPError`` the real client gets (gts-agency ``page_entries``, cf1d53a3).
    """

    def __init__(self) -> None:
        self._graphs: list[KnowledgeGraphEntry] = []
        #: Raised by every call while set (failure injection).
        self.error: Exception | None = None
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def add_graph(self, code: str, *, name: str = "", description: str = "", enabled: bool = True) -> None:
        self._graphs.append(KnowledgeGraphEntry(code=code, name=name or code, description=description, enabled=enabled))

    def list(self, organisation_id: int, *, page: int | None = None, size: int | None = None) -> KnowledgeGraphList:
        self.calls.append(("list", {"organisation_id": organisation_id, "page": page, "size": size}))
        if self.error is not None:
            raise self.error
        p = 0 if page is None else page
        s = 10 if size is None else size
        if p < 0 or s < 0 or p * s > _I32_MAX:
            raise http_error(500, _INVALID_PAGE_BODY)
        ordered = sorted(self._graphs, key=lambda g: g.code)
        start = min(p * s, len(ordered))
        return KnowledgeGraphList(page=DomainPage(page=p, size=s, total=len(ordered)), items=ordered[start : start + s])
