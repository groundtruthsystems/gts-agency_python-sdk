"""Offline DTO tests for agent gateway URL discovery.

Discovery DTOs are modeled from the control-plane Rust source
(agent_gateway_dto.rs:24-47) and live-verified 2026-07-07: the endpoint returns
a Page-wrapped payload and slots carry extra fields tolerated via
``extra="allow"``. Chat request/response DTOs no longer live here — the gateway
hands back official ``openai`` clients, which own those types.
"""

from agency_sdk.delegates.gateway_dto import (
    AgentGatewayEnvironmentResponse,
    AgentGatewayStatusResponse,
)


def test_discovery_status_parses_both_slots():
    payload = {
        "enabled": True,
        "code": "gateway-1a2b3c4d",
        "production": {
            "environment": "production",
            "status": "ready",
            "url": "https://agentgateway-org-2-wadexavawa-uk.a.run.app",
            "version": 3,
        },
        "test": {
            "environment": "test",
            "status": "ready",
            "url": "https://agentgateway-org-2-test-wadexavawa-uk.a.run.app",
            "version": 4,
        },
    }

    status = AgentGatewayStatusResponse(**payload)

    assert status.enabled is True
    assert status.production is not None
    assert status.production.url == "https://agentgateway-org-2-wadexavawa-uk.a.run.app"
    assert status.test is not None
    assert status.test.url == "https://agentgateway-org-2-test-wadexavawa-uk.a.run.app"


def test_discovery_status_tolerates_missing_slots_and_url():
    status = AgentGatewayStatusResponse(enabled=False)

    assert status.production is None
    assert status.test is None
    assert status.code is None

    provisioning = AgentGatewayEnvironmentResponse(environment="test", status="provisioning")
    assert provisioning.url is None
    assert provisioning.version is None


def test_discovery_status_retains_unknown_slot_fields():
    # Live payload carries id/vendor/runtime/manages_lifecycle beyond the modeled
    # fields — extra="allow" keeps them instead of rejecting the response.
    status = AgentGatewayStatusResponse(
        enabled=True,
        production={
            "environment": "production",
            "status": "ready",
            "url": "https://gw.example",
            "vendor": "agentgateway",
            "manages_lifecycle": True,
        },
    )

    assert status.production is not None
    assert status.production.model_dump()["vendor"] == "agentgateway"
