"""DTOs for agent gateway URL discovery.

Chat request/response modeling lives in the official ``openai`` SDK — the SDK's
gateway capability hands back configured ``openai`` clients, so the only DTOs it
owns are for control-plane discovery.

Discovery models mirror the control plane's ``AgentGatewayStatusResponse``
(agent_gateway_dto.rs:24-47) returned by ``GET /api/agentgateways?o={org}``,
carrying the per-environment (production/test) URLs. Live-verified 2026-07-07
against the control plane: the endpoint wraps items in the standard Page shape,
and slots carry extra fields (``id``, ``vendor``, ``runtime``,
``manages_lifecycle``, ...) tolerated via ``extra="allow"``.
"""

from pydantic import BaseModel, ConfigDict


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
