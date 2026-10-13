"""In-process fakes for the Agent Vault tests (no network, no comand).

``FakeVault`` is a duck-typed ``AgencySessionVaultClient`` that implements the backend contract in
``docs/agent_vault_gap_analysis.md`` §4: per-key monotonic ``version``, ``if_version`` (412) /
``if_absent`` (409) raised as ``ConditionalWriteFailed``, lexicographic ``prefix`` listing with
``page``/``size``, and ``scope=app|user`` held apart from the session's own keys. Every call is
appended to ``log`` so tests can assert order, parameters and request counts, and ``peak_inflight``
records the highest number of concurrent calls (the store's parallel-GET bound).

Hooks let a test inject the failure modes the store must survive:
- ``on_set(key, apply)``: runs instead of the plain write; call ``apply()`` to commit.
- ``get_delay``: seconds every ``get`` sleeps (to make concurrency observable).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

from agency_sdk.delegates.session_vault_client import ConditionalWriteFailed
from agency_sdk.delegates.session_vault_dto import (
    VaultEntryMeta,
    VaultEntryResponse,
    VaultListResponse,
    VaultSetResponse,
)
from agency_sdk.test.conftest import StubResponse


def http_error(status: int) -> requests.HTTPError:
    return requests.HTTPError(f"{status} error", response=StubResponse(json_data=None, status_code=status))


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


class FakeVault:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str | None, str], _Entry] = {}
        self.log: list[Call] = []
        self.on_set: Callable[[str, Callable[[], VaultSetResponse]], VaultSetResponse] | None = None
        self.get_delay = 0.0
        self.inflight = 0
        self.peak_inflight = 0
        self._lock = threading.Lock()

    # -- helpers ---------------------------------------------------------------------------------------
    def _enter(self, call: Call) -> None:
        with self._lock:
            self.log.append(call)
            self.inflight += 1
            self.peak_inflight = max(self.peak_inflight, self.inflight)

    def _exit(self) -> None:
        with self._lock:
            self.inflight -= 1

    def keys(self, session_id: str, scope: str | None = None) -> list[str]:
        return sorted(k for (s, sc, k) in self.data if s == session_id and sc == scope)

    def value(self, session_id: str, key: str, scope: str | None = None) -> Any:
        return self.data[(session_id, scope, key)].value

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
            entry = self.data.get((session_id, scope, key))
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
                with self._lock:
                    current = self.data.get((session_id, scope, key))
                    if if_absent and current is not None:
                        raise ConditionalWriteFailed("exists", response=StubResponse(status_code=409))
                    if if_version is not None and (current is None or current.version != if_version):
                        raise ConditionalWriteFailed("version_mismatch", response=StubResponse(status_code=412))
                    version = (current.version if current else 0) + 1
                    self.data[(session_id, scope, key)] = _Entry(value, classification or "restricted", version)
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
            keys = [k for k in self.keys(session_id, scope) if prefix is None or k.startswith(prefix)]
            if size is not None:
                start = (page or 0) * size
                keys = keys[start : start + size]
            return VaultListResponse(
                entries=[
                    VaultEntryMeta(
                        key=k,
                        size=len(str(self.data[(session_id, scope, k)].value)),
                        updated_at="t",
                        classification=self.data[(session_id, scope, k)].classification,
                        version=self.data[(session_id, scope, k)].version,
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
            self.data.pop((session_id, scope, key), None)
        finally:
            self._exit()
