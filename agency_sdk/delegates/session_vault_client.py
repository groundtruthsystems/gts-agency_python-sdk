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

from agency_sdk.delegates.base_client import BaseDelegateClient
from agency_sdk.delegates.session_vault_dto import (
    VaultEntryResponse,
    VaultListResponse,
    VaultSetResponse,
)

#: Maximum vault key length (mirrors the server's MAX_KEY_LEN).
MAX_KEY_LEN = 255


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
    if not all(c.isascii() and (c.isalnum() or c in "-_.") for c in key):
        raise ValueError(f"Invalid vault key: {key!r}")


class AgencySessionVaultClient(BaseDelegateClient):
    # vault writes send the raw JSON value (dict, list, or scalar) as the body; the
    # base _make_request already types ``data`` as ``Any``, so no override is needed.
    api_path = "/api/sessions"

    def list(self, organisation_id: int, session_id: str) -> VaultListResponse:
        """List all entries in a session vault (metadata only — no values).

        Args:
            organisation_id: The organisation ID (must match the session's).
            session_id: The session whose vault to list.
        """
        params = {"o": str(organisation_id)}
        return VaultListResponse(**self._make_request("GET", f"/{session_id}/vault", params=params))

    def get(
        self,
        organisation_id: int,
        session_id: str,
        key: str,
        reveal: bool = False,
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

        Raises:
            ValueError: If the key is invalid (raised before any network call).
            requests.HTTPError: 403 if access is denied by classification, 404
                if the entry does not exist.
        """
        _validate_key(key)
        params = {"o": str(organisation_id)}
        if reveal:
            params["reveal"] = "true"
        return VaultEntryResponse(**self._make_request("GET", f"/{session_id}/vault/{key}", params=params))

    def set(
        self,
        organisation_id: int,
        session_id: str,
        key: str,
        value: Any,
        classification: str | None = None,
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

        Raises:
            ValueError: If the key is invalid (raised before any network call).
            requests.HTTPError: 400 if the classification is unknown.
        """
        _validate_key(key)
        params = {"o": str(organisation_id)}
        if classification is not None:
            params["classification"] = classification
        result = self._make_request("PUT", f"/{session_id}/vault/{key}", data=value, params=params)
        return VaultSetResponse(**result)

    def delete(self, organisation_id: int, session_id: str, key: str) -> None:
        """Delete a vault entry. Succeeds even if the entry does not exist.

        Args:
            organisation_id: The organisation ID.
            session_id: The session whose vault to delete from.
            key: The entry key.

        Raises:
            ValueError: If the key is invalid (raised before any network call).
        """
        _validate_key(key)
        params = {"o": str(organisation_id)}
        self._make_request("DELETE", f"/{session_id}/vault/{key}", params=params)
