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

import json
from collections.abc import Iterator
from typing import Any

import requests

from agency_sdk.credentials import CredentialsSupplier
from agency_sdk.delegates.gateway_dto import ChatCompletionChunk, ChatCompletionRequest, ChatCompletionResponse


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
        """POST an OpenAI-compatible chat-completion request to the gateway (one-shot).

        Raises ``ValueError`` when the request carries a truthy ``stream`` —
        this method parses a single JSON body; use :meth:`chat_completions_stream`
        / :meth:`complete_stream`, or an openai client (``docs/gateway.md``).
        """
        if getattr(request, "stream", None):
            raise ValueError(
                "stream=True is not supported by chat_completions()/complete(); use "
                "chat_completions_stream()/complete_stream() or an openai client (see docs/gateway.md)"
            )
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

    def chat_completions_stream(self, request: ChatCompletionRequest) -> Iterator[ChatCompletionChunk]:
        """Stream a chat completion as SSE chunks (``stream: true`` is forced).

        Yields one :class:`ChatCompletionChunk` per ``data:`` event until
        ``data: [DONE]``. The HTTP response is closed when the stream is
        exhausted or the caller abandons the generator early.
        """
        body = request.model_dump(mode="json", by_alias=True, exclude_none=True)
        body["stream"] = True
        response = requests.post(
            f"{self.gateway_base_url}{self.api_path}/chat/completions",
            headers=self._headers(),
            json=body,
            timeout=120,
            stream=True,  # hand back the socket; iterate SSE lines instead of buffering
        )
        response.raise_for_status()
        try:
            # Byte mode + explicit UTF-8, deliberately NOT decode_unicode=True:
            # text/event-stream carries no charset, so requests defaults to
            # ISO-8859-1 — mojibake for UTF-8 content, and worse, a multibyte
            # char's 0x85 byte becomes U+0085 NEL, which str.splitlines() treats
            # as a line boundary and splits the JSON mid-string (found live
            # 2026-07-07). SSE is UTF-8 by spec; split on b"\n" only.
            for raw_line in response.iter_lines(delimiter=b"\n"):
                line = raw_line.decode("utf-8").strip()
                if not line or not line.startswith("data:"):
                    continue  # blank keep-alives and ": comment" lines
                payload = line[len("data:") :].strip()
                if payload == "[DONE]":
                    break
                yield ChatCompletionChunk(**json.loads(payload))
        finally:
            response.close()

    def complete_stream(self, messages: list[dict[str, Any]], model: str, **kw: Any) -> Iterator[str]:
        """Convenience: stream ``messages`` to ``model``, yielding assistant text deltas.

        Skips empty/role-only/usage-only chunks; extra keyword arguments pass
        through to the upstream provider (as with :meth:`complete`).
        """
        request = ChatCompletionRequest(model=model, messages=messages, **kw)
        for chunk in self.chat_completions_stream(request):
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
