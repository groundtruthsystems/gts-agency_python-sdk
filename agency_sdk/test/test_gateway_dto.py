"""Offline DTO tests for the agent gateway delegate.

Chat DTOs pin the OpenAI-compatible wire shape live-validated against the local
agentgateway v1.3.1 (docs/gateway_design.md §10); ``extra="allow"`` is the
contract — the exact field set is upstream, not owned by gts, so unknown
request params and response fields must pass through without breaking parsing.

Discovery DTOs are modeled from the control-plane Rust source
(agent_gateway_dto.rs:24-47) and are verification-deferred: offline-tested
here, live verification pending a control-plane image that ships the endpoint.
"""

from agency_sdk.delegates.gateway_dto import (
    AgentGatewayEnvironmentResponse,
    AgentGatewayStatusResponse,
    ChatChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
)

# The response shape observed live (§10): keys id/model/object/created/choices/
# usage/system_fingerprint/timings; `model` echoes the UPSTREAM name, not the
# virtual name sent.
LIVE_RESPONSE = {
    "id": "chatcmpl-abc123",
    "model": "Qwen3.6-27B-Instruct-Q5_K_M.gguf",
    "object": "chat.completion",
    "created": 1751846400,
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Extracted summary."},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 42, "completion_tokens": 7, "total_tokens": 49},
    "system_fingerprint": "b1234-abcdef",
    "timings": {"prompt_ms": 12.3, "predicted_ms": 456.7},
}


def test_chat_message_content_defaults_to_none():
    message = ChatMessage(role="assistant")

    assert message.role == "assistant"
    assert message.content is None


def test_request_accepts_and_dumps_extra_openai_params():
    request = ChatCompletionRequest(
        model="biglambda1",
        messages=[ChatMessage(role="user", content="hi")],
        temperature=0.0,
        max_tokens=64,
    )

    body = request.model_dump(mode="json", by_alias=True, exclude_none=True)

    assert body["model"] == "biglambda1"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["temperature"] == 0.0  # extra="allow" passes params through
    assert body["max_tokens"] == 64


def test_request_dump_excludes_none_content():
    request = ChatCompletionRequest(model="m", messages=[ChatMessage(role="assistant")])

    body = request.model_dump(mode="json", by_alias=True, exclude_none=True)

    assert body["messages"] == [{"role": "assistant"}]  # content=None dropped


def test_response_parses_live_validated_shape():
    response = ChatCompletionResponse(**LIVE_RESPONSE)

    assert response.choices[0].index == 0
    assert response.choices[0].message.content == "Extracted summary."
    # Unknown top-level fields (usage, timings, ...) are retained, not rejected.
    assert response.model_dump()["usage"]["total_tokens"] == 49


def test_response_tolerates_reasoning_only_empty_content():
    # Qwen quirk observed live (§10): too-small max_tokens -> content null,
    # reasoning_content only, finish_reason "length".
    payload = {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": None, "reasoning_content": "thinking..."},
                "finish_reason": "length",
            }
        ]
    }

    response = ChatCompletionResponse(**payload)

    assert response.choices[0].message.content is None


def test_chat_choice_index_defaults_to_zero():
    choice = ChatChoice(message=ChatMessage(role="assistant", content="x"))

    assert choice.index == 0


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
