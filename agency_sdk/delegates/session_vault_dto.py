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

from typing import Any

from pydantic import BaseModel


class Classification:
    """Vault entry classification levels.

    ``public``/``internal`` are stored as plaintext and viewable by any org
    member; ``confidential``/``restricted`` are encrypted at rest. Agents always
    read plaintext; humans never see ``restricted`` and may view
    ``confidential`` only via an audited reveal.
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"

    #: The default classification when none is supplied (most protective).
    DEFAULT = RESTRICTED


class VaultEntryMeta(BaseModel):
    """Metadata for one vault entry as returned by the list endpoint.

    Never carries the value, so listing exposes nothing sensitive.
    """

    key: str
    #: Size of the stored payload in bytes (ciphertext size for encrypted entries).
    size: int
    updated_at: str
    classification: str


class VaultListResponse(BaseModel):
    """Response body for listing all entries in a session vault."""

    entries: list[VaultEntryMeta]


class VaultEntryResponse(BaseModel):
    """A single vault entry's (decrypted) value."""

    key: str
    value: Any
    classification: str


class VaultSetResponse(BaseModel):
    """The stored entry's key and classification, returned by a write (PUT)."""

    key: str
    classification: str
