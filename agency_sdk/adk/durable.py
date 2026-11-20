"""``DurableRunner``: run an ADK agent by correlation id, resuming from the last checkpoint.

A correlation id names a **task**; several GTS sessions may share it (a work-queue *restart* or *reprocess*
starts a new session under the same id, a *resume* reopens the same one). ``run(correlation_id, ...)``
asks comand's ``resolve_session`` (``find_or_create`` on the service's template), which returns the task's
live session, else its newest finished one, else creates one; comand serialises concurrent creates, so
racing runners converge on one session. A dispatched agent passes its ``session_id`` and the dispatch
payload's ``correlation_id``: comand attaches the id to that session, or answers
:class:`~agency_sdk.delegates.session_templates_client.CorrelationMismatch` when it carries another.
A created or running session with no ADK head yet (just created, attached, or a restart's new session) is
initialised; one with a head is hydrated from the vault and continued:

- **completed** → the stored final result, without calling the model;
- **failed** → ``failed`` (the PHI-free ``adk.failure`` record names the error type);
- **awaiting input** → the session's human-input requests are read from comand: an expired one raises
  :class:`~agency_sdk.adk.errors.HitlExpired`; once *every* request of the paused invocation is answered,
  the recorded answers are injected as ``FunctionResponse`` parts and the run continues; otherwise
  ``awaiting_input`` is returned without running;
- **in progress** → resumed. The input is chosen by :func:`choose_resume_input`: an unfinished
  invocation resumes by ``invocation_id`` (ADK replays from its last committed event, so completed
  steps are not repeated); a committed final answer whose COMPLETED status was never posted is just
  completed; a fresh session starts with the caller's message.

A run that pauses on a long-running tool call (one whose function returns ``None``) creates one comand
request per pending call — comand sets ``AWAITING_INPUT`` — and returns ``awaiting_input``. Answers come
only from comand, where an authenticated human recorded them; :meth:`DurableRunner.resume` takes no
answer, and the committed ``FunctionResponse`` event is what stops an answer being injected twice.

Every committed event is a checkpoint (see :mod:`agency_sdk.adk.session_service`). Outcomes:

- a writer that loses the head race gets ``conflict`` and writes nothing further;
- an infrastructure error (vault or network) propagates and leaves the session ``IN_PROGRESS`` so a
  later run resumes it;
- an agent error writes ``adk.failure`` (exception *type* only — messages can carry PHI) and then marks
  the session FAILED. FAILED and COMPLETED are terminal in comand: an operator recovers the work item with
  ``resume`` (same session, a Failed vault session only) or ``restart`` (a new session, same correlation
  id), and the next ``run`` picks that session up.

Requires an ``App`` with ``ResumabilityConfig(is_resumable=True)`` and a ``GtsSessionService`` configured with
``template_name``. ``resolve_session`` answers only the template agent's service account, so run with
that account's credentials.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import requests
from google.adk.apps import App
from google.adk.errors import StaleSessionError
from google.adk.errors.already_exists_error import AlreadyExistsError
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import Session
from google.genai import types
from pydantic import BaseModel, Field

from agency_sdk.adk._records import FailureRecord
from agency_sdk.adk.errors import (
    AgentVaultError,
    DispatchedSessionMismatch,
    HitlExpired,
    RequestNotFound,
    SessionNotFound,
    VaultLimitExceeded,
)
from agency_sdk.adk.session_service import GtsSessionService
from agency_sdk.delegates.session_dto import SessionStatus
from agency_sdk.delegates.session_hitl_dto import HitlRequest
from agency_sdk.delegates.session_templates_client import ResolveMode
from agency_sdk.delegates.session_templates_dto import ResolvedSession

#: Default lifetime of a human-input request.
DEFAULT_HITL_TTL = timedelta(days=7)
#: Default cap on one human answer, checked before it is injected.
DEFAULT_MAX_ANSWER_BYTES = 64 * 1024

#: comand's "created" session status (dispatched, not yet reported on by the agent).
_CREATED = 1

OutcomeStatus = Literal["completed", "already_completed", "awaiting_input", "failed", "conflict"]


class RunOutcome(BaseModel):
    """What a durable run ended with. Carries identifiers and the final text only; ``repr`` hides the text."""

    status: OutcomeStatus
    session_id: str | None = None
    final_text: str | None = None
    error_type: str | None = None
    pending_requests: list[str] = Field(default_factory=list)
    #: The verified responders of the answers this run injected (recorded by comand).
    answered_by: list[str] = Field(default_factory=list)

    def __repr__(self) -> str:
        # final_text is the agent's answer and can carry PHI: never part of the repr.
        return (
            f"RunOutcome(status={self.status!r}, session_id={self.session_id!r}, error_type={self.error_type!r}, "
            f"pending_requests={len(self.pending_requests)})"
        )

    def __str__(self) -> str:
        return repr(self)


@dataclass(frozen=True)
class ResumeInput:
    """How to continue a session: a new message, an invocation to resume, or nothing to run."""

    new_message: types.Content | None = None
    invocation_id: str | None = None
    finished: bool = False
    paused: bool = False


@dataclass(frozen=True)
class PendingCall:
    """A long-running function call still waiting for its response."""

    call_id: str
    name: str
    args: dict[str, Any]
    invocation_id: str


def pending_calls(events: list[Event]) -> list[PendingCall]:
    """Long-running function calls that have no function response yet (a paused invocation)."""
    answered = {r.id for e in events for r in e.get_function_responses() if r.id}
    pending: list[PendingCall] = []
    for event in events:
        for fc in event.get_function_calls():
            if fc.id and event.long_running_tool_ids and fc.id in event.long_running_tool_ids and fc.id not in answered:
                pending.append(PendingCall(fc.id, fc.name or "", dict(fc.args or {}), event.invocation_id))
    return pending


def pending_long_running_calls(events: list[Event]) -> list[str]:
    """Ids of the pending long-running calls (see :func:`pending_calls`)."""
    return [c.call_id for c in pending_calls(events)]


def choose_resume_input(
    events: list[Event], *, answers: types.Content | None, message: types.Content | None
) -> ResumeInput:
    """Pick the run input, in priority order: answers to a paused call, the unfinished invocation, the message."""
    if answers is not None:
        return ResumeInput(new_message=answers)
    if not events:
        return ResumeInput(new_message=message)
    last = events[-1]
    if last.actions is not None and last.actions.end_of_agent:
        return ResumeInput(finished=True)
    if pending_long_running_calls(events):
        return ResumeInput(paused=True)
    return ResumeInput(invocation_id=last.invocation_id)


def final_text(events: list[Event]) -> str | None:
    """The text of the last final response in the history, if any."""
    for event in reversed(events):
        if event.content and event.is_final_response():
            parts = [p.text for p in event.content.parts or [] if p.text]
            if parts:
                return "".join(parts)
    return None


def _as_content(message: str | types.Content | None) -> types.Content | None:
    if message is None or isinstance(message, types.Content):
        return message
    return types.Content(role="user", parts=[types.Part(text=message)])


class DurableRunner:
    """Run an ADK app durably by correlation id (see the module docstring)."""

    def __init__(
        self,
        *,
        app: App,
        session_service: GtsSessionService,
        hitl: Any = None,
        hitl_ttl: timedelta = DEFAULT_HITL_TTL,
        max_answer_bytes: int = DEFAULT_MAX_ANSWER_BYTES,
    ) -> None:
        """
        Args:
            app: A resumable ADK app.
            session_service: The ``GtsSessionService`` the run persists through.
            hitl: The HITL delegate (``AgencyClient.session_hitl()``); required for pausing on human input.
            hitl_ttl: Default lifetime of a human-input request (overridable per run).
            max_answer_bytes: Cap on one human answer, checked before it is injected.

        Raises:
            ValueError: The app is not resumable.
        """
        if not (app.resumability_config and app.resumability_config.is_resumable):
            raise ValueError("DurableRunner needs App(resumability_config=ResumabilityConfig(is_resumable=True))")
        self.app = app
        self.session_service = session_service
        self.runner = Runner(app=app, session_service=session_service)
        self.hitl = hitl
        self.hitl_ttl = hitl_ttl
        self.max_answer_bytes = max_answer_bytes

    @property
    def _org(self) -> int:
        return self.session_service.organisation_id

    async def _update(self, session_id: str, status: SessionStatus, **fields: Any) -> None:
        await asyncio.to_thread(
            self.session_service.sessions.update, self._org, status=status, session_id=session_id, **fields
        )

    async def run(
        self,
        correlation_id: str | None,
        *,
        user_id: str,
        message: str | types.Content | None = None,
        session_id: str | None = None,
        hitl_ttl: timedelta | None = None,
    ) -> RunOutcome:
        """Run (or resume) the correlation id's task.

        Args:
            correlation_id: The task's id — ``[A-Za-z0-9._:-]{10,128}``, opaque, never a clinical identifier.
                A dispatched agent passes the ``correlation_id`` its worker put in the arguments; ``None`` (with
                ``session_id``) reads it from the dispatched session, for workers that do not pass it yet.
            user_id: The ADK user (the dispatch payload's ``user_id``); kept as framework metadata, never
                the session's owner, which comand sets.
            message: The input for a fresh run; ignored when an existing run is resumed.
            session_id: The dispatched GTS session. The run continues in exactly this session: comand returns it
                (it carries the correlation id) or attaches the id to it.
            hitl_ttl: Lifetime of any human-input request this run creates (default: the runner's).

        Raises:
            ValueError: ``correlation_id`` is ``None`` and no ``session_id`` was given.
            CorrelationMismatch: ``session_id`` already carries a different correlation id.
            DispatchedSessionMismatch: Another live session holds the correlation id; nothing was written.
            SessionNotFound: The run's session was deleted.
            HitlExpired: A human-input request of this run expired.
            RequestNotFound: A recorded answer does not belong to the paused call.
            AgentVaultError / requests exceptions: Infrastructure failures; the session stays in progress.
        """
        if correlation_id is None:
            if session_id is None:
                raise ValueError("run needs a correlation_id, or the dispatched session_id to read it from")
            dispatched = await asyncio.to_thread(self.session_service.sessions.get, session_id)
            correlation_id = dispatched.correlation_id
        resolved = await self._resolve("find_or_create", correlation_id, session_id=session_id, user_id=user_id)
        assert resolved is not None  # find_or_create never misses
        if session_id is not None and resolved.session_id != session_id:
            raise DispatchedSessionMismatch("another live session holds this task's correlation id")
        status = resolved.status
        # The ADK head is the only "set up" signal: ``created`` is false when comand finds the dispatched
        # session by its correlation id, and ``vault_version`` counts comand's own commits, not this head. Only a
        # created or running session is initialised (fresh, attached, dispatched, or a work-queue restart);
        # a finished one is never revived, and awaiting input needs a history.
        fresh = status in (_CREATED, SessionStatus.IN_PROGRESS)
        if fresh and await self.session_service.store.read_head(resolved.session_id) is None:
            try:
                await self.session_service.initialise_head(
                    app_name=self.app.name,
                    user_id=user_id,
                    session_id=resolved.session_id,
                    correlation_id=correlation_id,
                )
                status = int(SessionStatus.IN_PROGRESS)
            except AlreadyExistsError:
                pass  # a racing runner initialised it first; continue from what comand reported
        return await self._continue(resolved.session_id, status, user_id, _as_content(message), hitl_ttl)

    async def resume(self, correlation_id: str, *, user_id: str) -> RunOutcome:
        """Continue the correlation id's task with the answers comand has recorded — never with a caller's.

        Raises:
            SessionNotFound: No session has this correlation id (``resume`` never creates one), or it was deleted.
            HitlExpired / RequestNotFound: As for :meth:`run`.
        """
        resolved = await self._resolve("find", correlation_id)
        if resolved is None:
            raise SessionNotFound("no run has this correlation id")
        return await self._continue(resolved.session_id, resolved.status, user_id, None, None)

    async def _resolve(
        self, mode: ResolveMode, correlation_id: str, *, session_id: str | None = None, user_id: str | None = None
    ) -> ResolvedSession | None:
        template_name = self.session_service.template_name
        if not template_name:
            raise ValueError("DurableRunner needs a GtsSessionService configured with template_name")
        resolved: ResolvedSession | None = await asyncio.to_thread(
            self.session_service.templates.resolve_session,
            self._org,
            template_name,
            mode=mode,
            correlation_id=correlation_id,
            session_id=session_id,
            framework_user_id=user_id,
        )
        return resolved

    async def _load(self, session_id: str, user_id: str) -> Session:
        session = await self.session_service.get_session(app_name=self.app.name, user_id=user_id, session_id=session_id)
        if session is None:
            raise SessionNotFound(f"session {session_id} not found")
        return session

    async def _requests(self, session_id: str) -> list[HitlRequest]:
        if self.hitl is None:
            return []
        found: list[HitlRequest] = await asyncio.to_thread(self.hitl.list, self._org, session_id)
        return found

    async def _create_requests(self, session_id: str, calls: list[PendingCall], ttl: timedelta | None) -> RunOutcome:
        if self.hitl is None:
            raise ValueError("this run paused for human input but the runner has no hitl client")
        expires_at = datetime.now(timezone.utc) + (ttl or self.hitl_ttl)
        for pending in calls:
            await asyncio.to_thread(
                self.hitl.create,
                self._org,
                session_id,
                request_id=pending.call_id,
                invocation_id=pending.invocation_id,
                prompt={"tool": pending.name, "args": pending.args},
                expires_at=expires_at.isoformat(),
            )
        return RunOutcome(status="awaiting_input", session_id=session_id, pending_requests=[c.call_id for c in calls])

    def _answers(self, calls: list[PendingCall], requests_by_id: dict[str, HitlRequest]) -> types.Content:
        parts: list[types.Part] = []
        for pending in calls:
            request = requests_by_id[pending.call_id]
            if request.invocation_id != pending.invocation_id:
                raise RequestNotFound("a recorded answer does not belong to the paused invocation")
            try:
                size = len(json.dumps(request.response).encode())
            except (TypeError, ValueError):
                raise VaultLimitExceeded("a recorded answer is not JSON-serialisable") from None
            if size > self.max_answer_bytes:
                raise VaultLimitExceeded(f"an answer is {size} bytes; the limit is {self.max_answer_bytes}")
            response = request.response if isinstance(request.response, dict) else {"result": request.response}
            parts.append(
                types.Part(
                    function_response=types.FunctionResponse(id=pending.call_id, name=pending.name, response=response)
                )
            )
        return types.Content(role="user", parts=parts)

    async def _continue(
        self,
        session_id: str,
        status: int,
        user_id: str,
        message: types.Content | None,
        ttl: timedelta | None,
    ) -> RunOutcome:
        if status == SessionStatus.COMPLETED:
            done = await self._load(session_id, user_id)
            return RunOutcome(status="already_completed", session_id=session_id, final_text=final_text(done.events))
        requests_known: list[HitlRequest] | None = None
        if status in (SessionStatus.FAILED, SessionStatus.AWAITING_INPUT):
            requests_known = await self._requests(session_id)
            if any(r.status == "expired" for r in requests_known):
                raise HitlExpired("a human-input request of this run expired")
        if status == SessionStatus.FAILED:
            failure = await self.session_service.store.read_failure(session_id)
            return RunOutcome(
                status="failed", session_id=session_id, error_type=failure.error_type if failure else None
            )

        session = await self._load(session_id, user_id)
        failure = await self.session_service.store.read_failure(session_id)
        if failure is not None:  # crashed between the failure record and FAILED
            await self._update(session_id, SessionStatus.FAILED, error=failure.error_type)
            return RunOutcome(status="failed", session_id=session_id, error_type=failure.error_type)

        answers: types.Content | None = None
        answered_by: list[str] = []
        calls = pending_calls(session.events)
        if calls:
            if requests_known is None:
                requests_known = await self._requests(session_id)
            by_id = {r.request_id: r for r in requests_known}
            missing = [c for c in calls if c.call_id not in by_id]
            if missing:  # crashed after the pause committed, before the requests were created
                return await self._create_requests(session_id, calls, ttl)
            if any(by_id[c.call_id].status == "expired" for c in calls):
                raise HitlExpired("a human-input request of this run expired")
            if not all(by_id[c.call_id].status == "answered" for c in calls):
                return RunOutcome(
                    status="awaiting_input", session_id=session_id, pending_requests=[c.call_id for c in calls]
                )
            answers = self._answers(calls, by_id)
            answered_by = [by_id[c.call_id].responder or "" for c in calls]

        elif status == SessionStatus.AWAITING_INPUT:
            # Awaiting, yet nothing is paused in the history: an inconsistent state, so never run on it.
            return RunOutcome(status="awaiting_input", session_id=session_id)

        await self.session_service.repair_scoped_state(session)
        choice = choose_resume_input(session.events, answers=answers, message=message)
        if choice.finished:
            return await self._complete(session_id, session.events)
        if choice.new_message is None and choice.invocation_id is None:
            raise ValueError("a fresh run needs a message")
        outcome = await self._drive(session_id, user_id, choice, ttl)
        if answered_by:
            outcome.answered_by = answered_by
        return outcome

    async def _drive(self, session_id: str, user_id: str, choice: ResumeInput, ttl: timedelta | None) -> RunOutcome:
        await self._update(session_id, SessionStatus.IN_PROGRESS)
        last_invocation = choice.invocation_id
        try:
            async for event in self.runner.run_async(
                user_id=user_id,
                session_id=session_id,
                invocation_id=choice.invocation_id,
                new_message=choice.new_message,
            ):
                last_invocation = event.invocation_id or last_invocation
        except StaleSessionError:
            return RunOutcome(status="conflict", session_id=session_id)
        except (AgentVaultError, requests.RequestException):
            raise  # infrastructure: stay IN_PROGRESS so a later run resumes
        except Exception as exc:  # the agent itself failed: record the type only, then FAILED
            return await self._fail(session_id, type(exc).__name__, last_invocation)

        session = await self._load(session_id, user_id)
        calls = pending_calls(session.events)
        if calls:
            return await self._create_requests(session_id, calls, ttl)
        return await self._complete(session_id, session.events)

    async def _complete(self, session_id: str, events: list[Event]) -> RunOutcome:
        text = final_text(events)
        await self._update(session_id, SessionStatus.COMPLETED, result={"final_text": text})
        return RunOutcome(status="completed", session_id=session_id, final_text=text)

    async def _fail(self, session_id: str, error_type: str, invocation_id: str | None) -> RunOutcome:
        head = await self.session_service.store.read_head(session_id)
        record = FailureRecord(
            error_type=error_type,
            invocation_id=invocation_id,
            last_event_key=head.head.last_event_key if head else None,
            revision=head.head.revision if head else 0,
        )
        await self.session_service.store.write_failure(session_id, record)
        await self._update(session_id, SessionStatus.FAILED, error=error_type)
        return RunOutcome(status="failed", session_id=session_id, error_type=error_type)
