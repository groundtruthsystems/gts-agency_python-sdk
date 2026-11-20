"""Client for the session-templates API (`/api/session_templates`).

Lists an org's session templates so a caller can resolve a template NAME → id at runtime (the
guideline-agent dispatcher does this instead of hardcoding a template id), registers a new session from a
template, and resolves an agent's session by correlation id (``resolve_session``). Session creation lives
here rather than on the session-reporting delegate, which by design never mints a session.

A correlation id names a **task**: several sessions may share one (a work-queue restart or reprocess runs
a new session under the same id). comand sets every session's owner (``user_id``) itself; nothing here
sends one.
"""

import time
from typing import Any, Literal

import requests

from agency_sdk.delegates.base_client import BaseDelegateClient
from agency_sdk.delegates.session_templates_dto import (
    RegisteredSession,
    ResolvedSession,
    SessionTemplatesPagedResult,
)

#: ``resolve_session`` modes: ``find`` (miss → ``None``), ``create`` (an existing session →
#: :class:`SessionAlreadyExists`) and ``find_or_create``.
ResolveMode = Literal["find", "create", "find_or_create"]

#: Attempts at a ``resolve_session`` the server answers 503 (its per-template create lock is contended).
_RESOLVE_ATTEMPTS = 3
#: Upper bound on one ``Retry-After`` wait, so a misbehaving header cannot stall the caller.
_MAX_RETRY_AFTER_S = 5.0


class SessionTemplateNotFound(LookupError):
    """No session template in the organisation has the requested name."""


class CorrelationMismatch(requests.HTTPError):
    """409 ``correlation_mismatch``: the dispatched session already carries a different correlation id.

    Use the ``correlation_id`` from the dispatch payload as the framework session id. The message never
    repeats either id.
    """


class SessionAlreadyExists(requests.HTTPError):
    """409 ``already_exists``: ``resolve_session(mode="create")`` found a session for the correlation id."""


class AgencySessionTemplatesClient(BaseDelegateClient):
    api_path = "/api/session_templates"

    def list(self, organisation_id: int, *, page: int = 0, size: int = 50) -> SessionTemplatesPagedResult:
        """List the org's session templates (paged), for resolving a template NAME → id.

        Name→id matching (and any cache / not-found / duplicate handling) is the
        caller's job; this returns the raw page.
        """
        params = {"o": str(organisation_id), "p": str(page), "s": str(size)}
        return SessionTemplatesPagedResult(**self._make_request("GET", "", params=params))

    def resolve_id(self, organisation_id: int, name: str) -> str:
        """Return the id of the template named ``name``, paging through the org's templates.

        Raises:
            SessionTemplateNotFound: No template has that name.
        """
        page = 0
        while True:
            result = self.list(organisation_id, page=page)
            for template in result.items:
                if template.name == name:
                    return template.id
            if not result.items or (page + 1) * result.page.size >= result.page.total:
                raise SessionTemplateNotFound(f"No session template named {name!r}")
            page += 1

    def register(
        self,
        organisation_id: int,
        template_name: str,
        *,
        correlation_id: str | None = None,
        input: dict[str, Any] | None = None,
    ) -> RegisteredSession:
        """Register a new session from the template named ``template_name``.

        ``correlation_id`` is sent as ``input.correlation``; without it the session's correlation id is its
        own id. Registering never conflicts: many sessions may share a correlation id. comand sets the owner
        to the calling principal.

        Args:
            organisation_id: The organisation ID.
            template_name: The template's name, resolved to its id via :meth:`resolve_id`.
            correlation_id: The task's id: ``[A-Za-z0-9._:-]{10,128}``, opaque, never a clinical identifier.
            input: The session input payload.

        Raises:
            SessionTemplateNotFound: No template has that name (raised before the register call).
            requests.HTTPError: 400 for an invalid correlation id, or any other error.
        """
        template_id = self.resolve_id(organisation_id, template_name)
        session_input: dict[str, Any] = dict(input or {})
        if correlation_id is not None:
            session_input["correlation"] = correlation_id
        payload = {"command": "register", "organisation": organisation_id, "register": {"input": session_input}}
        body = self._make_request("POST", f"/{template_id}/_command", data=payload, params={"o": str(organisation_id)})
        return RegisteredSession(**body["register"])

    def resolve_session(
        self,
        organisation_id: int,
        template_name: str,
        *,
        mode: ResolveMode,
        correlation_id: str,
        session_id: str | None = None,
        input: dict[str, Any] | None = None,
        state: dict[str, Any] | None = None,
        framework_user_id: str | None = None,
    ) -> ResolvedSession | None:
        """Find, create or attach the agent session for ``correlation_id`` on this template.

        The lookup is (organisation, template, correlation id): a live session (created, running or awaiting
        input) wins, otherwise the newest finished one. ``find_or_create`` never creates when one exists, and
        comand serialises concurrent creates on the template, so racing callers get one session.

        Only the template agent's service account (or the session's bound agent) may call this; any other
        caller gets the same 404 as a miss.

        Args:
            organisation_id: The organisation ID.
            template_name: The template (the ADK app), resolved via :meth:`resolve_id`.
            mode: ``find``, ``create`` or ``find_or_create``.
            correlation_id: The task's id. Written ids need ``[A-Za-z0-9._:-]{10,128}``; ``find`` accepts
                ``{1,128}``.
            session_id: Attach the correlation id to this dispatched session instead of creating one.
            input: Session input for a newly created session.
            state: Initial agent state, applied on create only.
            framework_user_id: The framework's user id, kept only as metadata; it never sets the owner.

        Returns:
            The session, or ``None`` when ``find`` matches nothing (or the caller may not resolve).

        Raises:
            CorrelationMismatch: ``session_id`` already carries a different correlation id.
            SessionAlreadyExists: ``mode="create"`` and a session already exists.
            SessionTemplateNotFound: No template has that name.
            requests.HTTPError: 503 after the retries, a 404 outside ``find``, or any other error.
        """
        template_id = self.resolve_id(organisation_id, template_name)
        command: dict[str, Any] = {"mode": mode, "correlation_id": correlation_id}
        if session_id is not None:
            command["session_id"] = session_id
        if input is not None:
            command["input"] = input
        if state is not None:
            command["state"] = state
        if framework_user_id is not None:
            command["user_id"] = framework_user_id
        payload = {"command": "resolve_session", "organisation": organisation_id, "resolve_session": command}
        params = {"o": str(organisation_id)}
        for attempt in range(1, _RESOLVE_ATTEMPTS + 1):
            try:
                body = self._make_request("POST", f"/{template_id}/_command", data=payload, params=params)
                return ResolvedSession(**body["resolve_session"])
            except requests.HTTPError as err:
                status = err.response.status_code if err.response is not None else None
                if status == 404 and mode == "find":
                    return None
                if status == 409:
                    code = _error_code(err.response)
                    if code == "correlation_mismatch":
                        raise CorrelationMismatch(
                            "The session already carries a different correlation id; use the dispatched one",
                            response=err.response,
                        ) from None
                    if code == "already_exists":
                        raise SessionAlreadyExists(
                            "A session already exists for this correlation id", response=err.response
                        ) from None
                if status == 503 and attempt < _RESOLVE_ATTEMPTS:
                    time.sleep(_retry_after(err.response))
                    continue
                raise
        raise AssertionError("unreachable")  # pragma: no cover


def _error_code(response: requests.Response | None) -> str | None:
    """Read ``error.details.code`` from the standard error envelope, if present."""
    if response is None:
        return None
    try:
        details = response.json().get("error", {}).get("details", {})
    except (ValueError, AttributeError):
        return None
    code = details.get("code") if isinstance(details, dict) else None
    return code if isinstance(code, str) else None


def _retry_after(response: requests.Response | None) -> float:
    """Seconds to wait from ``Retry-After`` (default 1), capped at :data:`_MAX_RETRY_AFTER_S`."""
    raw = response.headers.get("Retry-After") if response is not None else None
    try:
        seconds = float(raw) if raw is not None else 1.0
    except ValueError:
        seconds = 1.0
    return min(max(seconds, 0.0), _MAX_RETRY_AFTER_S)
