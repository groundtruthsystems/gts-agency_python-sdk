"""Client for the session-templates API (`/api/session_templates`).

A thin read-only delegate whose one job is to list an org's session templates so
a caller can resolve a template NAME → id at runtime (the guideline-agent
dispatcher does this instead of hardcoding a template id in its config). Its own
API domain — distinct from the session-reporting delegate (`/api/sessions`).
"""

from agency_sdk.delegates.base_client import BaseDelegateClient
from agency_sdk.delegates.session_templates_dto import SessionTemplatesPagedResult


class AgencySessionTemplatesClient(BaseDelegateClient):
    api_path = "/api/session_templates"

    def list(self, organisation_id: int, *, page: int = 0, size: int = 50) -> SessionTemplatesPagedResult:
        """List the org's session templates (paged), for resolving a template NAME → id.

        Name→id matching (and any cache / not-found / duplicate handling) is the
        caller's job; this returns the raw page.
        """
        params = {"o": str(organisation_id), "p": str(page), "s": str(size)}
        return SessionTemplatesPagedResult(**self._make_request("GET", "", params=params))
