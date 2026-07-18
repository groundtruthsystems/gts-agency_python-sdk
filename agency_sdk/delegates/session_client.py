"""Client for reporting progress on a dispatched control-plane session.

An agent NEVER creates its own session. Both the direct-uri and work-queue
dispatch paths inject the session id into the agent's arguments (the ① worker
sets ``arguments.session_id``); the agent ``attach``es to that inherited session
and reports progress via ``update``. Self-registration would mint a second
session divorced from the work-item card (the "orphan leg ②"), so ``register``
is intentionally not exposed here — see ``docs/session_reporting_delegate_design.md``.

The client only MARSHALS a caller-decided status; it never infers the run
outcome. Deciding the status (and any terminal payload) stays with the agent.
"""

from typing import Any

from agency_sdk.credentials import CredentialsSupplier
from agency_sdk.delegates.base_client import BaseDelegateClient
from agency_sdk.delegates.session_dto import SessionCommandResponse, SessionStatus


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
