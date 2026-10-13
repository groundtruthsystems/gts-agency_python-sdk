"""Client for human-input (HITL) requests on a session (``/api/sessions/{id}/hitl``).

The agent side creates a request when a run pauses on a long-running tool call and reads requests back
to resume; comand atomically moves the session to ``AWAITING_INPUT`` on create, and a background job
expires requests that pass ``expires_at`` (server time).

The human side answers with :meth:`AgencySessionHitlClient.respond`. Build that client with the
**human's own** token supplier: comand authorises the answer by backend policy, rejects service
principals (so an agent's client-credentials token can never answer), keeps only the first answer and
records the verified ``responder``. The agent never sends an answer itself.

Contract: ``docs/agent_vault_gap_analysis.md`` §4 item 6.
"""

from datetime import datetime
from typing import Any

import requests

from agency_sdk.delegates.base_client import BaseDelegateClient
from agency_sdk.delegates.session_hitl_dto import HitlRequest, HitlRequestList


class HitlAlreadyAnswered(requests.HTTPError):
    """The request already has an answer (HTTP 409); the first answer stands."""


class HitlRequestExpired(requests.HTTPError):
    """The request expired before it was answered (HTTP 410)."""


class HitlNotAuthorised(requests.HTTPError):
    """The caller may not answer this request (HTTP 403) — for example a service principal."""


_RESPOND_ERRORS: dict[int, type[requests.HTTPError]] = {
    409: HitlAlreadyAnswered,
    410: HitlRequestExpired,
    403: HitlNotAuthorised,
}


class AgencySessionHitlClient(BaseDelegateClient):
    api_path = "/api/sessions"

    def create(
        self,
        organisation_id: int,
        session_id: str,
        *,
        request_id: str,
        invocation_id: str,
        prompt: dict[str, Any],
        expires_at: datetime | str,
    ) -> HitlRequest:
        """Create (or return the existing) request ``request_id`` and set the session ``AWAITING_INPUT``.

        Idempotent on ``request_id``, so it opts into connection-retry.
        """
        body = {
            "organisation": organisation_id,
            "request_id": request_id,
            "invocation_id": invocation_id,
            "prompt": prompt,
            "expires_at": expires_at.isoformat() if isinstance(expires_at, datetime) else expires_at,
        }
        params = {"o": str(organisation_id)}
        return HitlRequest(**self._make_request("POST", f"/{session_id}/hitl", data=body, params=params, retry=True))

    def get(self, organisation_id: int, session_id: str, request_id: str) -> HitlRequest:
        """Read one request, including the answer and verified responder once answered."""
        params = {"o": str(organisation_id)}
        return HitlRequest(**self._make_request("GET", f"/{session_id}/hitl/{request_id}", params=params))

    def list(self, organisation_id: int, session_id: str, *, status: str | None = None) -> list[HitlRequest]:
        """List the session's requests, optionally filtered by ``pending``/``answered``/``expired``."""
        params = {"o": str(organisation_id)}
        if status is not None:
            params["status"] = status
        return HitlRequestList(**self._make_request("GET", f"/{session_id}/hitl", params=params)).items

    def respond(self, organisation_id: int, session_id: str, request_id: str, response: Any) -> HitlRequest:
        """Answer a request as the human this client's token belongs to.

        Raises:
            HitlAlreadyAnswered: Someone answered first (409).
            HitlRequestExpired: The request expired (410).
            HitlNotAuthorised: Backend policy refuses this principal (403), e.g. a service principal.
        """
        body = {"organisation": organisation_id, "response": response}
        params = {"o": str(organisation_id)}
        try:
            result = self._make_request("POST", f"/{session_id}/hitl/{request_id}/_respond", data=body, params=params)
        except requests.HTTPError as err:
            status = err.response.status_code if err.response is not None else None
            if status in _RESPOND_ERRORS:
                raise _RESPOND_ERRORS[status](str(status), response=err.response) from err
            raise
        return HitlRequest(**result)
