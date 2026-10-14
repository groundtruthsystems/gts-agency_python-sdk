"""DTOs for the session vault API (snake_case, matching the API).

The session vault is an organisation- and session-scoped key/value store of
serializable JSON, backed by object storage. Each entry carries a
classification controlling encryption and read access.

Wire format: the vault endpoints serialise with plain snake_case field names
(no camelCase), so these models use raw snake_case fields with no alias
generator. This intentionally follows the same convention as ``files_dto.py``
and ``datasets_dto.py`` — not the ``alias_generator=_to_camel`` convention used
by the camelCase APIs (datasource, ontology, rules). See CLAUDE.md ("DTOs")
for the split.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class Classification(StrEnum):
    """Vault entry classification levels.

    ``public``/``internal`` are stored as plaintext and viewable by any org
    member; ``confidential``/``restricted`` are encrypted at rest. Agents always
    read plaintext; humans never see ``restricted`` and may view
    ``confidential`` only via an audited reveal.

    A ``StrEnum`` so members are ``str`` (``Classification.PUBLIC == "public"``,
    ``str(Classification.PUBLIC) == "public"``) and serialise directly as query
    params, while giving callers a typed, enumerable set of valid values.
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"

    #: The default classification when none is supplied (most protective).
    #: An alias of ``RESTRICTED`` (``Classification.DEFAULT is Classification.RESTRICTED``).
    DEFAULT = RESTRICTED


class VaultScope(StrEnum):
    """Cross-session scopes on the session vault (``?scope=``), for ADK ``app:``/``user:`` state.

    The server resolves the app (from the session's template or agent) and the user (the session's bound
    owner) itself and applies backend policy, so a caller can never name another app or user. A ``user``
    read or write fails closed (4xx) when the session has no bound owner.
    """

    APP = "app"
    USER = "user"


class VaultEntryMeta(BaseModel):
    """Metadata for one vault entry as returned by the list endpoint.

    Never carries the value, so listing exposes nothing sensitive.
    """

    key: str
    #: Size of the stored payload in bytes (ciphertext size for encrypted entries).
    size: int
    updated_at: str
    classification: str
    #: Monotonic per-key version (``None`` from servers that predate versioning).
    version: int | None = None


class VaultListResponse(BaseModel):
    """Response body for listing all entries in a session vault."""

    entries: list[VaultEntryMeta]


class VaultEntryResponse(BaseModel):
    """A single vault entry's (decrypted) value."""

    key: str
    value: Any
    classification: str
    #: Monotonic per-key version, the precondition for ``set(if_version=...)``.
    version: int | None = None


class VaultSetResponse(BaseModel):
    """The stored entry's key, classification and new version, returned by a write (PUT)."""

    key: str
    classification: str
    version: int | None = None
