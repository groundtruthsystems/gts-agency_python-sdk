"""Vault key scheme and record codec for ADK sessions (design §Data Architecture).

One ADK session is one GTS session; its vault holds:

- ``adk.session`` — the **head**: identity, ``revision``, ``event_count``, ``last_event_key`` and the
  session-scoped state. Conditional writes on the head are the only commit point.
- ``adk.ev.<seq:08d>.<event_id>`` — one immutable record per event, linked to its predecessor by
  ``prev_key``. An event is invisible until a head names it, so a CAS loser's event is an unreachable
  orphan rather than corruption.
- ``adk.failure`` — a write-once failure summary with no payloads.
- ``adk.state`` under ``scope=app|user`` — ADK ``app:``/``user:`` state, resolved by comand.

Events are encoded with ``model_dump_json(by_alias=True)`` (no ``exclude_none``) and decoded with
``model_validate_json``: ADK's ``val_json_bytes='base64'`` applies only in JSON mode, so validating the
decoded dict instead would corrupt ``inline_data`` bytes.

Records override ``repr``/``str`` so state, ``user_id`` and event content never reach a log by accident.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from google.adk.events import Event
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_core import PydanticSerializationError

from agency_sdk.adk.errors import CorruptRecord, InvalidState, UnsupportedSchema

#: Highest record schema this SDK writes and understands.
SCHEMA_VERSION = 1

HEAD_KEY = "adk.session"
EVENT_PREFIX = "adk.ev."
FAILURE_KEY = "adk.failure"
STATE_KEY = "adk.state"

_KEY_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
_MAX_ID_IN_KEY = 200


def event_key(seq: int, event_id: str) -> str:
    """The vault key of event number ``seq`` (1-based).

    Zero-padding keeps keys in sequence order under the server's lexicographic listing. An id outside
    the key charset (or too long) is replaced by its SHA-256 so the key stays valid and unique.
    """
    if not event_id or len(event_id) > _MAX_ID_IN_KEY or not set(event_id) <= _KEY_CHARS:
        event_id = hashlib.sha256(event_id.encode()).hexdigest()
    return f"{EVENT_PREFIX}{seq:08d}.{event_id}"


class _Record(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    def _safe_fields(self) -> str:
        return ""

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._safe_fields()})"

    def __str__(self) -> str:
        return repr(self)


class SessionHead(_Record):
    """The session head (``adk.session``). ``user_id`` is PII and ``state`` is PHI-capable."""

    schema_version: int = Field(default=SCHEMA_VERSION, alias="schema")
    app_name: str
    user_id: str
    correlation_id: str | None = None
    revision: int = 0
    event_count: int = 0
    last_event_key: str | None = None
    last_update_time: float
    state: dict[str, Any] = Field(default_factory=dict)
    #: Set by ``delete_session``: a tombstone, so history is kept for forensics but reads return ``None``.
    deleted: bool = False

    def _safe_fields(self) -> str:
        return f"revision={self.revision}, event_count={self.event_count}, deleted={self.deleted}"


class EventRecord(_Record):
    """One stored event (``adk.ev.*``)."""

    seq: int
    prev_key: str | None
    event: dict[str, Any]

    def _safe_fields(self) -> str:
        return f"seq={self.seq}"


class FailureRecord(_Record):
    """A PHI-free failure summary (``adk.failure``): the exception *type* only, never its message."""

    error_type: str
    invocation_id: str | None = None
    last_event_key: str | None = None
    revision: int
    at: float = Field(default_factory=time.time)

    def _safe_fields(self) -> str:
        return f"error_type={self.error_type!r}, revision={self.revision}"


def to_wire(record: _Record) -> dict[str, Any]:
    """Serialise a record to the JSON value stored in the vault.

    Raises:
        InvalidState: The record holds a value that is not JSON-native (for example in session state).
    """
    try:
        value: dict[str, Any] = json.loads(record.model_dump_json(by_alias=True))
    except PydanticSerializationError:
        raise InvalidState(f"{type(record).__name__} holds a value that is not JSON-serialisable") from None
    return value


def encode_event(event: Event) -> dict[str, Any]:
    """Encode an ADK event for storage (JSON mode, by alias, ``None`` fields kept)."""
    value: dict[str, Any] = json.loads(event.model_dump_json(by_alias=True))
    return value


def decode_event(value: Any) -> Event:
    """Decode a stored event; any malformed payload is a :class:`CorruptRecord`."""
    try:
        return Event.model_validate_json(json.dumps(value))
    except (ValidationError, TypeError, ValueError) as exc:
        raise CorruptRecord(f"stored event is malformed ({type(exc).__name__})") from None


def _check_schema(value: Any, kind: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CorruptRecord(f"stored {kind} is not an object")
    schema = value.get("schema", SCHEMA_VERSION)
    if isinstance(schema, int) and schema > SCHEMA_VERSION:
        raise UnsupportedSchema(f"stored {kind} uses schema {schema}; this SDK understands up to {SCHEMA_VERSION}")
    return value


def decode_head(value: Any) -> SessionHead:
    try:
        record: SessionHead = SessionHead.model_validate(_check_schema(value, "session head"))
        return record
    except ValidationError as exc:
        raise CorruptRecord(f"stored session head is malformed ({exc.error_count()} errors)") from None


def decode_event_record(value: Any) -> EventRecord:
    try:
        record: EventRecord = EventRecord.model_validate(_check_schema(value, "event record"))
        return record
    except ValidationError as exc:
        raise CorruptRecord(f"stored event record is malformed ({exc.error_count()} errors)") from None


def decode_failure(value: Any) -> FailureRecord:
    try:
        record: FailureRecord = FailureRecord.model_validate(_check_schema(value, "failure record"))
        return record
    except ValidationError as exc:
        raise CorruptRecord(f"stored failure record is malformed ({exc.error_count()} errors)") from None
