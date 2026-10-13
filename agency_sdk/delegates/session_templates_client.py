"""Client for the session-templates API (`/api/session_templates`).

Lists an org's session templates so a caller can resolve a template NAME → id at runtime
(the guideline-agent dispatcher does this instead of hardcoding a template id), and
registers a new session from a template by name. Registration lives here rather than
on the session-reporting delegate, which by design never mints a session.
"""

from typing import Any

import requests

from agency_sdk.delegates.base_client import BaseDelegateClient
from agency_sdk.delegates.session_templates_dto import SessionTemplatesPagedResult


class SessionTemplateNotFound(LookupError):
    """No session template in the organisation has the requested name."""


class CorrelationIdConflict(requests.HTTPError):
    """A session already owns this correlation id (HTTP 409); ``existing_session_id`` names it.

    The message never repeats the correlation id, which may be sensitive.
    """

    def __init__(self, existing_session_id: str | None, *, response: requests.Response | None = None) -> None:
        super().__init__("A session already exists for this correlation id", response=response)
        self.existing_session_id = existing_session_id


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
        correlation_id: str,
        labels: dict[str, str] | None = None,
        input: dict[str, Any] | None = None,
        owner: str | None = None,
    ) -> str:
        """Register a new session from the template named ``template_name``; return its session id.

        The correlation id is sent as the ``correlation_id`` label, which the server keeps unique per
        organisation, so racing registrations for one id yield one session and a 409 for the rest.

        Args:
            organisation_id: The organisation ID.
            template_name: The template's name, resolved to its id via :meth:`resolve_id`.
            correlation_id: The run's correlation id.
            labels: Extra labels (for example ``adk_app``) used by label search.
            input: The session input payload.
            owner: The end user the session acts for (the dispatch claim the server binds as owner).

        Raises:
            SessionTemplateNotFound: No template has that name (raised before the register call).
            CorrelationIdConflict: A session already owns ``correlation_id``.
        """
        template_id = self.resolve_id(organisation_id, template_name)
        register: dict[str, Any] = {
            "input": input or {},
            "labels": {**(labels or {}), "correlation_id": correlation_id},
        }
        if owner is not None:
            register["owner"] = owner
        payload = {"command": "register", "organisation": organisation_id, "register": register}
        try:
            body = self._make_request(
                "POST", f"/{template_id}/_command", data=payload, params={"o": str(organisation_id)}
            )
        except requests.HTTPError as err:
            if err.response is not None and err.response.status_code == 409:
                raise CorrelationIdConflict(_existing_session_id(err.response), response=err.response) from err
            raise
        session_id: str = body["register"]["session_id"]
        return session_id


def _existing_session_id(response: requests.Response) -> str | None:
    """Read ``error.details.session_id`` from the standard error envelope, if present."""
    try:
        details = response.json().get("error", {}).get("details", {})
    except (ValueError, AttributeError):
        return None
    value = details.get("session_id") if isinstance(details, dict) else None
    return value if isinstance(value, str) else None
