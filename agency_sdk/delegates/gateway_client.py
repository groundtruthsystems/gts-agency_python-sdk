"""OpenAI-client factory for the org's deployed agentgateway.

``AgencyGatewayClient`` is not itself an HTTP client — it wires the gateway's own
Cloud Run host (never the control-plane ``base_url``), the shared rotating m2m
JWT, and the ``x-org`` routing header into standard official ``openai`` clients
and hands them back. Consumers use the returned ``openai.OpenAI`` /
``openai.AsyncOpenAI`` for the full OpenAI surface (completions, streaming,
tools, structured outputs, retries, async).

Auth contract (live-validated, design §10): the shared ``CredentialsSupplier``
JWT as ``Authorization: Bearer`` — stamped fresh per request by an httpx auth
hook so a long-running client never sends an expired token — plus
``x-org: <org id>`` (lowercase header, decimal-string value). Missing Bearer →
401, missing/wrong ``x-org`` → 403 (``openai`` raises ``APIStatusError``).
"""

from typing import TYPE_CHECKING, Any

from agency_sdk.credentials import CredentialsSupplier

if TYPE_CHECKING:
    import openai


class AgencyGatewayClient:
    """Factory for official ``openai`` clients routed through the org's agentgateway."""

    def __init__(self, token_supplier: CredentialsSupplier, gateway_base_url: str, org_id: str):
        self.gateway_base_url = gateway_base_url.rstrip("/")
        self.token_supplier = token_supplier
        self.org_id = org_id  # org scoping is the x-org header, not a query param

    def openai_client(self, **kwargs: Any) -> "openai.OpenAI":
        """Build a standard ``openai.OpenAI`` client wired to this gateway.

        The returned client is plain openai SDK — streaming, tool calling,
        structured outputs, retries all work as documented upstream — with the
        gateway plumbing pre-wired: ``base_url`` on the gateway host (``/v1``),
        the ``x-org`` routing header, and an httpx auth hook that stamps a fresh
        rotating bearer on every request (the construction-time ``api_key`` is a
        placeholder the hook overrides). Extra ``kwargs`` (``max_retries``,
        ``timeout``, ...) pass through to ``openai.OpenAI`` — except the four
        this helper wires itself (``base_url``, ``api_key``, ``default_headers``,
        ``http_client``); passing those raises ``TypeError``, so build your own
        client (see ``docs/gateway.md``) if you need to control them.

        Each call returns a new client; the caller owns its lifecycle.
        """
        import httpx
        import openai

        return openai.OpenAI(
            base_url=f"{self.gateway_base_url}/v1",
            api_key="unused-auth-via-httpx-hook",  # placeholder; the hook overrides per request
            default_headers={"x-org": self.org_id},
            http_client=httpx.Client(auth=self._httpx_bearer_auth()),
            **kwargs,
        )

    def async_openai_client(self, **kwargs: Any) -> "openai.AsyncOpenAI":
        """Async variant of :meth:`openai_client` (``openai.AsyncOpenAI``).

        Note: the bearer re-mint inside the auth hook is a synchronous (cheap,
        cached) call; it blocks the event loop only on the periodic refresh.
        """
        import httpx
        import openai

        return openai.AsyncOpenAI(
            base_url=f"{self.gateway_base_url}/v1",
            api_key="unused-auth-via-httpx-hook",  # placeholder; the hook overrides per request
            default_headers={"x-org": self.org_id},
            http_client=httpx.AsyncClient(auth=self._httpx_bearer_auth()),
            **kwargs,
        )

    def _httpx_bearer_auth(self) -> Any:
        """Per-request rotating-bearer httpx auth (shared core hook)."""
        from agency_sdk.auth_hooks import make_httpx_bearer_auth

        return make_httpx_bearer_auth(self.token_supplier.bearer_token)
