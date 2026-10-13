"""Typed errors raised by the Agent Vault ADK integration.

Messages carry identifiers, keys, sizes and status codes only — never a payload, state value,
``user_id`` or correlation id (the redaction rule in the design's security section). Stale writes are
not here: they raise ADK's own ``google.adk.errors.StaleSessionError`` so ADK's runner semantics apply.
"""


class AgentVaultError(Exception):
    """Base class for Agent Vault errors."""


class CorruptRecord(AgentVaultError):
    """A stored record could not be decoded, or the event chain is broken (cycle, gap, wrong length)."""


class UnsupportedSchema(CorruptRecord):
    """A stored record was written by a newer schema than this SDK understands; refused rather than guessed."""


class VaultLimitExceeded(AgentVaultError):
    """A write would exceed a configured size or event-count cap. Raised before any request is sent."""


class InvalidState(AgentVaultError):
    """Session state holds a value that is not JSON-native and cannot be stored losslessly."""


class VaultUnavailable(AgentVaultError):
    """The vault call failed (transport error or unexpected HTTP status). ``status_code`` is set when known.

    Raised without the underlying ``requests`` exception chained, because its text carries request URLs.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


__all__ = [
    "AgentVaultError",
    "CorruptRecord",
    "UnsupportedSchema",
    "VaultLimitExceeded",
    "InvalidState",
    "VaultUnavailable",
]
