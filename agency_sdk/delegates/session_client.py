"""Client for reporting progress on a dispatched control-plane session.

An agent NEVER creates its own session. Both the direct-uri and work-queue
dispatch paths inject the session id into the agent's arguments (the ① worker
sets ``arguments.session_id``); the agent ``attach``es to that inherited session
and reports progress via ``update``. Self-registration would mint a second
session divorced from the work-item card (the "orphan leg ②"), so ``register``
is intentionally not exposed here — see ``docs/session_reporting_delegate_design.md``.
(A session the SDK itself must create — an ADK ``create_session`` with no id — is registered from a
template via ``AgencySessionTemplatesClient.register``, never from here.)

``search`` lists sessions by task (``correlation_id``), owner (``user_id``), template or status. A
correlation id names a task that many sessions may share; an agent locates *its* session by correlation id
with ``AgencySessionTemplatesClient.resolve_session`` instead. Selectors travel in the POST body, so a
correlation id never lands in a URL, access log or ``HTTPError`` message.

The client only MARSHALS a caller-decided status; it never infers the run
outcome. Deciding the status (and any terminal payload) stays with the agent.
"""

from typing import Any

from agency_sdk.credentials import CredentialsSupplier
from agency_sdk.delegates.base_client import BaseDelegateClient
from agency_sdk.delegates.session_dto import (
    SessionCommandResponse,
    SessionSearchResult,
    SessionStatus,
    SessionSummary,
)


class AgencySessionClient(BaseDelegateClient):
    api_path = "/api/sessions"

    def __init__(self, token_supplier: CredentialsSupplier, base_url: str = "http://localhost:9003"):
        super().__init__(token_supplier, base_url=base_url)
        self._session_id: str | None = None

    @property
    def session_id(self) -> str | None:
        """The attached session id, or ``None`` if nothing has been attached yet."""
        return self._session_id

    def attach(self, session_id: str) -> None:
        """Bind this client to an EXISTING dispatched session (inherit, not create).

        Records ``session_id`` as the default target for subsequent ``update``
        calls. No HTTP request — the session already exists; the agent inherits
        it rather than registering a new one.
        """
        self._session_id = session_id

    def update(
        self,
        organisation_id: int,
        *,
        status: SessionStatus | int,
        result: dict[str, Any] | None = None,
        events: list[dict[str, Any]] | None = None,
        metrics: dict[str, Any] | None = None,
        error: str | None = None,
        logs: str | None = None,
        session_id: str | None = None,
    ) -> SessionCommandResponse:
        """Report progress on the session via ``POST /{id}/_command {command:"update"}``.

        Args:
            organisation_id: The organisation ID (query ``o`` and body ``organisation``).
            status: The caller-decided status — a ``SessionStatus`` or a raw int.
                Marshalled as-is (``FAILED`` -1 / ``COMPLETED`` 0 / ``IN_PROGRESS`` 2);
                the SDK never infers it from the other fields.
            result: Optional terminal result payload.
            events: Optional already-serialized analytics events (the agent owns
                batching/serialization; see ``AnalyticsEvent``).
            metrics: Optional run metrics.
            error: Optional failure detail (surfaced in the CP UI's ERRORS tab).
            logs: Optional captured logs (surfaced in the CP UI's LOGS tab).
            session_id: Target session; defaults to the ``attach``ed session. An
                explicit value overrides the attached session for THIS call only —
                it does not rebind ``self._session_id``.

        Raises:
            ValueError: If no session is attached and no ``session_id`` is given
                (raised before any network call).
        """
        target = session_id or self._session_id
        if target is None:
            raise ValueError("no session attached; call attach(session_id) first or pass session_id=")
        update: dict[str, Any] = {"status": int(status)}
        if result is not None:
            update["result"] = result
        if events is not None:
            update["events"] = events
        if metrics is not None:
            update["metrics"] = metrics
        if error is not None:
            update["error"] = error
        if logs is not None:
            update["logs"] = logs
        payload = {"command": "update", "organisation": organisation_id, "update": update}
        # retry=True: re-sending the same update is safe (idempotent; ①'s session-status
        # monotonicity guard prevents terminal regression), unlike the work-queue POSTs.
        body = self._make_request(
            "POST", f"/{target}/_command", data=payload, params={"o": str(organisation_id)}, retry=True
        )
        return SessionCommandResponse(**body)

    def get(self, session_id: str) -> SessionSummary:
        """Read one session via ``GET /api/sessions/{id}`` (comand resolves the organisation from the record).

        A dispatched agent uses it to learn its task's ``correlation_id`` when the worker did not pass one.

        Raises:
            requests.HTTPError: 404 when the session does not exist or the caller may not read it.
        """
        return SessionSummary(**self._make_request("GET", f"/{session_id}"))

    def search(
        self,
        organisation_id: int,
        *,
        correlation_id: str | None = None,
        user_id: str | None = None,
        template_id: str | None = None,
        status: list[SessionStatus | int] | None = None,
        page: int = 0,
        size: int = 50,
        sort: tuple[str, str] | None = None,
    ) -> SessionSearchResult:
        """Search the organisation's sessions via ``POST /api/sessions/_search`` (newest first by default).

        Every filter is exact and optional; the server applies the caller's organisations and the review
        exclusion on top. Selectors travel in the POST body.

        Args:
            organisation_id: The organisation to search (sent as the one-element ``organisation`` list).
            correlation_id: Sessions of this task (case-sensitive).
            user_id: Sessions owned by this principal (``U:<id>``, ``sa:<id>`` or ``key:<id>``). This is the
                owner comand set, not a framework user id.
            template_id: Sessions of this template (for ADK, the app).
            status: Only these status codes.
            page: 0-based page number.
            size: Page size.
            sort: ``(field, direction)``, e.g. ``("created_on", "asc")``; the server default is newest first.

        Raises:
            requests.HTTPError: 400 for a malformed ``correlation_id``/``user_id``, or any other error.
        """
        body: dict[str, Any] = {"organisation": [organisation_id], "page": {"page": page, "size": size}}
        if correlation_id is not None:
            body["correlation_id"] = correlation_id
        if user_id is not None:
            body["user_id"] = user_id
        if template_id is not None:
            body["template_id"] = template_id
        if status is not None:
            body["status"] = [int(s) for s in status]
        if sort is not None:
            body["sort"] = {"field": sort[0], "direction": sort[1]}
        params = {"o": str(organisation_id)}
        return SessionSearchResult(**self._make_request("POST", "/_search", data=body, params=params, retry=True))
