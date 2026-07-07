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
from typing import TYPE_CHECKING, Any

import requests

from agency_sdk.credentials import CredentialsSupplier
from agency_sdk.delegates.gateway_dto import ChatCompletionChunk, ChatCompletionRequest, ChatCompletionResponse

if TYPE_CHECKING:
    import openai

_OPENAI_INSTALL_HINT = (
    "openai_client()/async_openai_client() need the openai package; "
    "install the SDK's [openai] extra: pip install gts-agency-python-sdk[openai]"
)


def _require_openai() -> None:
    """Fail fast with an actionable message when the ``[openai]`` extra is absent."""
    try:
        import openai  # noqa: F401
    except ImportError as exc:
        raise ImportError(_OPENAI_INSTALL_HINT) from exc


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
        try:
            response.raise_for_status()  # inside try: error responses are closed too, not left to GC
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

        First choice only (``index == 0``, as with :meth:`complete`); skips
        empty/role-only/usage-only chunks — a reasoning model that spends its
        whole token budget on ``reasoning_content`` yields nothing. Extra
        keyword arguments pass through to the upstream provider.
        """
        request = ChatCompletionRequest(model=model, messages=messages, **kw)
        for chunk in self.chat_completions_stream(request):
            for choice in chunk.choices:
                if choice.index == 0 and choice.delta.content:
                    yield choice.delta.content

    def openai_client(self, **kwargs: Any) -> "openai.OpenAI":
        """Build a standard ``openai.OpenAI`` client wired to this gateway (full-feature path).

        The returned client is plain openai SDK — streaming, tool calling,
        structured outputs, retries all work as documented upstream — with the
        gateway plumbing pre-wired: ``base_url`` on the gateway host (``/v1``),
        the ``x-org`` routing header, and an httpx auth hook that stamps a
        fresh rotating bearer on every request (the construction-time
        ``api_key`` is a placeholder). Extra ``kwargs`` (``max_retries``,
        ``timeout``, ...) pass through to ``openai.OpenAI`` — except the four
        this helper wires itself (``base_url``, ``api_key``, ``default_headers``,
        ``http_client``); passing those raises ``TypeError`` — build your own
        client instead (docs/gateway.md tier C) if you need to control them.

        Each call returns a new client; the caller owns its lifecycle.
        Requires the ``[openai]`` extra.
        """
        _require_openai()
        import httpx
        import openai

        return openai.OpenAI(
            base_url=f"{self.gateway_base_url}/v1",
            api_key="rotating-bearer-via-auth-hook",  # placeholder; the hook overrides per request
            default_headers={"x-org": self.org_id},
            http_client=httpx.Client(auth=self._httpx_bearer_auth()),
            **kwargs,
        )

    def async_openai_client(self, **kwargs: Any) -> "openai.AsyncOpenAI":
        """Async variant of :meth:`openai_client` (``openai.AsyncOpenAI``).

        Note: the bearer re-mint inside the auth hook is a synchronous (cheap,
        cached) call; it blocks the event loop only on the periodic refresh.
        """
        _require_openai()
        import httpx
        import openai

        return openai.AsyncOpenAI(
            base_url=f"{self.gateway_base_url}/v1",
            api_key="rotating-bearer-via-auth-hook",  # placeholder; the hook overrides per request
            default_headers={"x-org": self.org_id},
            http_client=httpx.AsyncClient(auth=self._httpx_bearer_auth()),
            **kwargs,
        )

    def _httpx_bearer_auth(self) -> Any:
        """Per-request rotating-bearer httpx auth (shared core hook, no observability dep)."""
        from agency_sdk.auth_hooks import make_httpx_bearer_auth

        return make_httpx_bearer_auth(self.token_supplier.bearer_token)
