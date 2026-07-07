"""OpenAI-compatible client for the org's deployed agentgateway.

A deliberate sibling of :class:`~agency_sdk.delegates.base_client.BaseDelegateClient`,
not a subclass: the gateway lives on its own Cloud Run host (never the
control-plane ``base_url``), uses the fixed ``/v1`` OpenAI path, needs a much
longer timeout, and stamps the extra ``x-org`` routing header that the base
header set does not carry (docs/gateway_design.md §5.2).

Auth contract (live-validated, design §10): the shared ``CredentialsSupplier``
JWT as ``Authorization: Bearer`` plus ``x-org: <org id>`` (lowercase header,
decimal-string value). Missing Bearer → 401, missing/wrong ``x-org`` → 403,
both with plain-text bodies — errors propagate via ``raise_for_status()``.
"""

from typing import Any

import requests

from agency_sdk.credentials import CredentialsSupplier
from agency_sdk.delegates.gateway_dto import ChatCompletionRequest, ChatCompletionResponse


class AgencyGatewayClient:
    """OpenAI-compatible LLM client routed through the org's agentgateway."""

    #: OpenAI-compatible path prefix on the gateway host.
    api_path = "/v1"

    def __init__(self, token_supplier: CredentialsSupplier, gateway_base_url: str, org_id: str):
        self.gateway_base_url = gateway_base_url.rstrip("/")
        self.token_supplier = token_supplier
        self.org_id = org_id  # org scoping is the x-org header, not a query param

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token_supplier.bearer_token()}",
            "Content-Type": "application/json",
            "x-org": self.org_id,  # gateway authz rule (template.rs:16); NOT x-org-id
        }

    def chat_completions(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """POST an OpenAI-compatible chat-completion request to the gateway."""
        response = requests.post(
            f"{self.gateway_base_url}{self.api_path}/chat/completions",
            headers=self._headers(),
            json=request.model_dump(mode="json", by_alias=True, exclude_none=True),
            timeout=120,  # LLM calls are slow; the 30s delegate default is too tight
        )
        response.raise_for_status()
        return ChatCompletionResponse(**response.json())

    def complete(self, messages: list[dict[str, Any]], model: str, **kw: Any) -> str:
        """Convenience: send ``messages`` to ``model`` and return the assistant text.

        Extra keyword arguments (``temperature``, ``max_tokens``, ...) pass
        through to the upstream provider. Returns ``""`` when the assistant
        content is null (e.g. reasoning-only truncation, design §10).
        """
        request = ChatCompletionRequest(model=model, messages=messages, **kw)
        response = self.chat_completions(request)
        return response.choices[0].message.content or ""
