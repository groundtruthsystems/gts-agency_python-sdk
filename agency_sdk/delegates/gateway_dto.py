"""DTOs for the agent gateway delegate (snake_case, OpenAI-compatible wire).

Chat models mirror the OpenAI Chat Completions shape proxied by the deployed
``agentgateway`` binary. The exact request/response field set is agentgateway
upstream — not owned by the gts repos — so every model here is deliberately
``extra="allow"``: unknown request params (``temperature``, ``max_tokens``,
``response_format``, ...) and unknown response fields (``usage``, ``timings``,
``reasoning_content``, ...) pass through without breaking parsing
(docs/gateway_design.md §5.5, §8.9; shape live-validated in §10).

Discovery models mirror the control plane's ``AgentGatewayStatusResponse``
(agent_gateway_dto.rs:24-47) returned by ``GET /api/agentgateways?o={org}``,
carrying the per-environment (production/test) Cloud Run URLs. Source-modeled;
live verification deferred (docs/gateway_design.md §4.1, §10 decision 1).
"""

from pydantic import BaseModel, ConfigDict


class ChatMessage(BaseModel):
    """One chat turn; ``content`` may be null (e.g. reasoning-only truncation)."""

    model_config = ConfigDict(extra="allow")

    role: str
    content: str | None = None


class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible request; extra params pass through to the upstream."""

    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[ChatMessage]


class ChatChoice(BaseModel):
    model_config = ConfigDict(extra="allow")

    index: int = 0
    message: ChatMessage


class ChatCompletionResponse(BaseModel):
    """OpenAI-compatible response; assistant text at ``choices[0].message.content``."""

    model_config = ConfigDict(extra="allow")

    choices: list[ChatChoice]


class AgentGatewayEnvironmentResponse(BaseModel):
    """One deployment slot (production or test) with its Cloud Run URL."""

    model_config = ConfigDict(extra="allow")

    environment: str
    status: str
    url: str | None = None
    version: int | None = None


class AgentGatewayStatusResponse(BaseModel):
    """Per-org gateway status: enabled flag plus the two environment slots."""

    model_config = ConfigDict(extra="allow")

    enabled: bool
    code: str | None = None
    production: AgentGatewayEnvironmentResponse | None = None
    test: AgentGatewayEnvironmentResponse | None = None
