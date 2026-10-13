"""In-process (optionally file-backed) fakes of comand for the Agent Vault tests — no network.

``FakeVault`` is a duck-typed ``AgencySessionVaultClient`` implementing the backend contract in
``docs/agent_vault_gap_analysis.md`` §4: per-key monotonic ``version``, ``if_version`` (412) /
``if_absent`` (409) raised as ``ConditionalWriteFailed``, lexicographic ``prefix`` listing with
``page``/``size``, and ``scope=app|user`` resolved *server-side*. Every call is appended to ``log``
and ``peak_inflight`` records the highest number of concurrent calls.

``FakeControlPlane`` adds the session and session-template endpoints (labels, correlation lookup,
search, register, status updates) and owns a ``FakeVault`` whose ``app``/``user`` scopes resolve the
way comand does: ``user`` to the session's bound owner (403 when none is bound — fail closed) and
``app`` to the session's ``adk_app`` label.

Pass ``path=`` to persist everything to a JSON file under an exclusive ``flock``, so separate
processes (the cross-process read-back and SIGKILL scenarios) share one fake backend.

Hooks for failure injection: ``FakeVault.on_set(key, apply)`` runs instead of a plain write (call
``apply()`` to commit); ``FakeVault.get_delay`` makes every ``get`` sleep.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

import requests

from agency_sdk.delegates.datasets_dto import Page
from agency_sdk.delegates.session_dto import SessionCommandResponse, SessionRef, SessionSearchResult
from agency_sdk.delegates.session_templates_client import CorrelationIdConflict, SessionTemplateNotFound
from agency_sdk.delegates.session_vault_client import ConditionalWriteFailed
from agency_sdk.delegates.session_vault_dto import (
    VaultEntryMeta,
    VaultEntryResponse,
    VaultListResponse,
    VaultSetResponse,
)
from agency_sdk.test.conftest import StubResponse


def http_error(status: int, body: Any = None) -> requests.HTTPError:
    return requests.HTTPError(f"{status} error", response=StubResponse(json_data=body, status_code=status))


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
        self.log: list[tuple[str, dict[str, Any]]] = []

    def _scope_space(self, session_id: str, scope: str) -> str | None:
        session = self.shared.sessions.get(session_id)
        if session is None:
            return None
        if scope == "user":
            owner = session.get("owner")
            return f"user:{owner}" if owner else None
        app = session.get("labels", {}).get("adk_app")
        return f"app:{app}" if app else None

    def add_session(
        self, session_id: str, *, owner: str | None = None, labels: dict[str, str] | None = None, status: int = 2
    ) -> None:
        """Seed a dispatched session (as the ① worker would have created it)."""
        with self.shared.io():
            self.shared.sessions[session_id] = {"status": status, "labels": dict(labels or {}), "owner": owner}

    def session(self, session_id: str) -> dict[str, Any]:
        with self.shared.io():
            return json.loads(json.dumps(self.shared.sessions[session_id]))


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
        labels: dict[str, str] | None = None,
        session_id: str | None = None,
    ) -> SessionCommandResponse:
        self._cp.log.append(
            ("update", {"session_id": session_id, "status": int(status), "labels": labels, "result": result})
        )
        with self._cp.shared.io():
            session = self._cp.shared.sessions.get(session_id or "")
            if session is None:
                raise http_error(404)
            terminal = session["status"] in (-1, 0)
            if not (terminal and int(status) not in (-1, 0)):  # comand's stale-regression guard
                session["status"] = int(status)
            if labels:
                session["labels"].update(labels)
            if result is not None:
                session["result"] = result
        return SessionCommandResponse(success=True, message="ok")

    def find_by_correlation_id(self, organisation_id: int, correlation_id: str) -> SessionRef | None:
        self._cp.log.append(("lookup", {}))
        with self._cp.shared.io():
            for sid, s in self._cp.shared.sessions.items():
                if s["labels"].get("correlation_id") == correlation_id:
                    return SessionRef(session_id=sid, status=s["status"], labels=s["labels"])
        return None

    def search(
        self, organisation_id: int, *, labels: dict[str, str], user: str | None = None, page: int = 0, size: int = 50
    ) -> SessionSearchResult:
        self._cp.log.append(("search", {"labels": labels, "user": user}))
        with self._cp.shared.io():
            matches = [
                SessionRef(session_id=sid, status=s["status"], labels=s["labels"])
                for sid, s in sorted(self._cp.shared.sessions.items())
                if all(s["labels"].get(k) == v for k, v in labels.items()) and (user is None or s.get("owner") == user)
            ]
        items = matches[page * size : (page + 1) * size]
        return SessionSearchResult(page=Page(total=len(matches), page=page, size=size), items=items)


class _FakeTemplates:
    def __init__(self, cp: FakeControlPlane) -> None:
        self._cp = cp

    def register(
        self,
        organisation_id: int,
        template_name: str,
        *,
        correlation_id: str,
        labels: dict[str, str] | None = None,
        input: dict[str, Any] | None = None,
        owner: str | None = None,
    ) -> str:
        self._cp.log.append(("register", {"template": template_name, "labels": labels, "owner": owner}))
        with self._cp.shared.io():
            if template_name not in self._cp.shared.templates:
                raise SessionTemplateNotFound(f"No session template named {template_name!r}")
            for sid, s in self._cp.shared.sessions.items():
                if s["labels"].get("correlation_id") == correlation_id:
                    raise CorrelationIdConflict(sid)
            sid = f"s-{uuid.uuid4().hex[:12]}"
            self._cp.shared.sessions[sid] = {
                "status": 2,
                "labels": {**(labels or {}), "correlation_id": correlation_id},
                "owner": owner,
            }
            return sid
