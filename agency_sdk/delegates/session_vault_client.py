"""Client for the session vault API (/api/sessions/{id}/vault).

The session vault is an organisation- and session-scoped key/value store of
serializable JSON, backed by object storage. Autonomous agents use it to
persist state mid-run — for example, saving a checkpoint before handing off to
a human-in-the-loop (HITL) and resuming from that state once the human has done
their part.

Entries carry a classification (default ``restricted``): ``public``/``internal``
are stored as plaintext and viewable; ``confidential``/``restricted`` are
encrypted at rest. Agents always read plaintext; humans never see ``restricted``
and may view ``confidential`` only via an audited reveal (``reveal=True``).
"""

from typing import Any

import requests

from agency_sdk.delegates.base_client import BaseDelegateClient
from agency_sdk.delegates.session_vault_dto import (
    VaultEntryResponse,
    VaultListResponse,
    VaultScope,
    VaultSetResponse,
)

#: Maximum vault key length (mirrors the server's MAX_KEY_LEN).
MAX_KEY_LEN = 255


def _is_key_chars(value: str) -> bool:
    return all(c.isascii() and (c.isalnum() or c in "-_.") for c in value)


def _validate_key(key: str) -> None:
    """Validate a vault key client-side, before any network call.

    Keys map directly onto object-storage paths, so the server restricts them
    to ``[A-Za-z0-9._-]`` (non-empty, <= 255 chars, not "." or "..") to prevent
    path traversal. Mirroring that here fails fast with a clear error.

    Raises:
        ValueError: If the key is empty, too long, "." / "..", or contains a
            character outside the allowed set.
    """
    if not key or len(key) > MAX_KEY_LEN or key in (".", ".."):
        raise ValueError(f"Invalid vault key: {key!r}")
    if not _is_key_chars(key):
        raise ValueError(f"Invalid vault key: {key!r}")


def _validate_prefix(prefix: str) -> None:
    """A list prefix obeys the key charset (it is matched against keys server-side)."""
    if not prefix or len(prefix) > MAX_KEY_LEN or not _is_key_chars(prefix):
        raise ValueError(f"Invalid vault key prefix: {prefix!r}")


def _scope_param(scope: VaultScope | str | None) -> str | None:
    """Normalise ``scope`` to its wire value, rejecting anything but ``app``/``user`` before any call."""
    if scope is None:
        return None
    try:
        return str(VaultScope(scope))
    except ValueError:
        raise ValueError(f"Invalid vault scope: {scope!r} (expected 'app' or 'user')") from None


class ConditionalWriteFailed(requests.HTTPError):
    """A conditional vault write lost its precondition — control flow, not a failure of the call.

    ``reason`` is ``"version_mismatch"`` (HTTP 412, ``if_version`` no longer current) or ``"exists"``
    (HTTP 409, ``if_absent`` but the key is already there). Subclasses ``HTTPError`` so callers that
    only know the base convention still see an HTTP error.
    """

    def __init__(self, reason: str, *, response: requests.Response | None = None) -> None:
        super().__init__(f"Conditional vault write failed: {reason}", response=response)
        self.reason = reason


_CONDITIONAL_REASONS = {412: "version_mismatch", 409: "exists"}


class AgencySessionVaultClient(BaseDelegateClient):
    # vault writes send the raw JSON value (dict, list, or scalar) as the body; the
    # base _make_request already types ``data`` as ``Any``, so no override is needed.
    api_path = "/api/sessions"

    def list(
        self,
        organisation_id: int,
        session_id: str,
        *,
        prefix: str | None = None,
        page: int | None = None,
        size: int | None = None,
        scope: VaultScope | str | None = None,
        timeout: float | None = None,
    ) -> VaultListResponse:
        """List entries in a session vault (metadata only — no values).

        Args:
            organisation_id: The organisation ID (must match the session's).
            session_id: The session whose vault to list.
            prefix: Only list keys starting with this prefix (lexicographic order).
            page: Zero-based page number (``p``).
            size: Page size (``s``).
            scope: List the session's ``app`` or ``user`` scope instead of its own keys.

        Raises:
            ValueError: If the prefix or scope is invalid (raised before any network call).
        """
        params = {"o": str(organisation_id)}
        if prefix is not None:
            _validate_prefix(prefix)
            params["prefix"] = prefix
        if page is not None:
            params["p"] = str(page)
        if size is not None:
            params["s"] = str(size)
        scope_value = _scope_param(scope)
        if scope_value is not None:
            params["scope"] = scope_value
        return VaultListResponse(**self._make_request("GET", f"/{session_id}/vault", params=params, timeout=timeout))

    def get(
        self,
        organisation_id: int,
        session_id: str,
        key: str,
        reveal: bool = False,
        *,
        scope: VaultScope | str | None = None,
        timeout: float | None = None,
    ) -> VaultEntryResponse:
        """Read a single vault entry's value.

        Access depends on the caller and the entry's classification: agents
        always read the plaintext value; a human may read ``public``/``internal``
        freely, must pass ``reveal=True`` for ``confidential`` (audited), and
        cannot read ``restricted`` at all.

        Args:
            organisation_id: The organisation ID.
            session_id: The session whose vault to read.
            key: The entry key.
            reveal: Pass ``True`` to reveal a ``confidential`` value as a human
                (the access is audited). Ignored for agent callers.
            scope: Read from the session's ``app`` or ``user`` scope (resolved server-side).

        Raises:
            ValueError: If the key is invalid (raised before any network call).
            requests.HTTPError: 403 if access is denied by classification, 404
                if the entry does not exist.
        """
        _validate_key(key)
        params = {"o": str(organisation_id)}
        if reveal:
            params["reveal"] = "true"
        scope_value = _scope_param(scope)
        if scope_value is not None:
            params["scope"] = scope_value
        return VaultEntryResponse(
            **self._make_request("GET", f"/{session_id}/vault/{key}", params=params, timeout=timeout)
        )

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
        scope: VaultScope | str | None = None,
        timeout: float | None = None,
    ) -> VaultSetResponse:
        """Create or overwrite a vault entry. The body is the raw JSON value.

        Args:
            organisation_id: The organisation ID.
            session_id: The session whose vault to write to.
            key: The entry key.
            value: Any JSON-serializable value (dict, list, or scalar).
            classification: One of ``public``, ``internal``, ``confidential``,
                ``restricted``. Defaults server-side to ``restricted`` when
                omitted. See ``session_vault_dto.Classification``.
            if_version: Write only if the entry's current ``version`` equals this (optimistic
                concurrency). The server answers 412 otherwise.
            if_absent: Write only if the key does not exist yet. The server answers 409 otherwise.
            scope: Write to the session's ``app`` or ``user`` scope (resolved server-side).

        A conditional write is safe to repeat, so it opts into connection-retry. After a retry a
        lost precondition may mean the *first* attempt committed; callers re-read to tell the two apart.

        Raises:
            ValueError: If the key or scope is invalid, or both conditions are given (raised before
                any network call).
            ConditionalWriteFailed: The ``if_version``/``if_absent`` precondition did not hold.
            requests.HTTPError: 400 if the classification is unknown.
        """
        _validate_key(key)
        if if_version is not None and if_absent:
            raise ValueError("if_version and if_absent are mutually exclusive")
        params = {"o": str(organisation_id)}
        if classification is not None:
            params["classification"] = classification
        conditional = if_version is not None or if_absent
        if if_version is not None:
            params["if_version"] = str(if_version)
        if if_absent:
            params["if_absent"] = "true"
        scope_value = _scope_param(scope)
        if scope_value is not None:
            params["scope"] = scope_value
        try:
            result = self._make_request(
                "PUT",
                f"/{session_id}/vault/{key}",
                data=value,
                params=params,
                retry=True if conditional else None,
                timeout=timeout,
            )
        except requests.HTTPError as err:
            status = err.response.status_code if err.response is not None else None
            if conditional and status in _CONDITIONAL_REASONS:
                raise ConditionalWriteFailed(_CONDITIONAL_REASONS[status], response=err.response) from err
            raise
        return VaultSetResponse(**result)

    def delete(
        self,
        organisation_id: int,
        session_id: str,
        key: str,
        *,
        scope: VaultScope | str | None = None,
        timeout: float | None = None,
    ) -> None:
        """Delete a vault entry. Succeeds even if the entry does not exist.

        Args:
            organisation_id: The organisation ID.
            session_id: The session whose vault to delete from.
            key: The entry key.
            scope: Delete from the session's ``app`` or ``user`` scope (resolved server-side).

        Raises:
            ValueError: If the key or scope is invalid (raised before any network call).
        """
        _validate_key(key)
        params = {"o": str(organisation_id)}
        scope_value = _scope_param(scope)
        if scope_value is not None:
            params["scope"] = scope_value
        self._make_request("DELETE", f"/{session_id}/vault/{key}", params=params, timeout=timeout)
