"""Wire-contract tests for AgencyGatewayClient (offline; requests stubbed).

Pins the gateway call contract live-validated in docs/gateway_design.md §10:
POST {gateway_base_url}/v1/chat/completions with Bearer + x-org (lowercase,
decimal-string org id — NOT x-org-id, the observability OTLP header), JSON
body from model_dump(mode="json", by_alias=True, exclude_none=True), 120s
timeout, and plain-text (non-JSON) 401/403 error bodies surfacing via
raise_for_status.
"""

import pytest
import requests

from agency_sdk.delegates.gateway_client import AgencyGatewayClient
from agency_sdk.delegates.gateway_dto import ChatCompletionRequest, ChatMessage

COMPLETION = {
    "id": "chatcmpl-1",
    "model": "Qwen3.6-27B-Instruct-Q5_K_M.gguf",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}],
}


@pytest.fixture
def client(fake_credentials):
    return AgencyGatewayClient(
        token_supplier=fake_credentials,
        gateway_base_url="http://gw.test:4000/",
        org_id="2",
    )


def test_init_rstrips_gateway_url_and_binds_org(client, fake_credentials):
    assert client.gateway_base_url == "http://gw.test:4000"
    assert client.org_id == "2"
    assert client.token_supplier is fake_credentials


def test_chat_completions_posts_to_gateway_v1_path(stub_requests, client):
    stub_requests.queue(json_data=COMPLETION)

    request = ChatCompletionRequest(model="biglambda1", messages=[ChatMessage(role="user", content="hi")])
    response = client.chat_completions(request)

    assert response.choices[0].message.content == "hello"
    call = stub_requests.calls[0]
    assert call.method == "POST"
    assert call.url == "http://gw.test:4000/v1/chat/completions"  # gateway host, not control plane
    assert call.kwargs["timeout"] == 120  # LLM calls are slow; 30s base default is too tight


def test_chat_completions_headers_carry_bearer_content_type_and_x_org(stub_requests, client):
    stub_requests.queue(json_data=COMPLETION)

    client.chat_completions(ChatCompletionRequest(model="m", messages=[ChatMessage(role="user", content="q")]))

    headers = stub_requests.calls[0].kwargs["headers"]
    assert headers["Authorization"] == "Bearer test-token"
    assert headers["Content-Type"] == "application/json"
    assert headers["x-org"] == "2"  # exact lowercase header (template.rs:16); not x-org-id
    assert "x-org-id" not in headers


def test_chat_completions_body_includes_extras_and_excludes_none(stub_requests, client):
    stub_requests.queue(json_data=COMPLETION)

    request = ChatCompletionRequest(
        model="biglambda1",
        messages=[ChatMessage(role="user", content="hi")],
        temperature=0.0,
    )
    client.chat_completions(request)

    body = stub_requests.calls[0].kwargs["json"]
    assert body["model"] == "biglambda1"
    assert body["temperature"] == 0.0  # extra OpenAI params pass through
    assert body["messages"] == [{"role": "user", "content": "hi"}]  # content=None keys dropped


def test_complete_returns_first_choice_text(stub_requests, client):
    stub_requests.queue(json_data=COMPLETION)

    text = client.complete([{"role": "user", "content": "hi"}], model="biglambda1", temperature=0.0)

    assert text == "hello"
    body = stub_requests.calls[0].kwargs["json"]
    assert body["model"] == "biglambda1"
    assert body["temperature"] == 0.0


def test_complete_returns_empty_string_on_null_content(stub_requests, client):
    # Qwen reasoning quirk (§10): finish_reason=length -> content null.
    stub_requests.queue(
        json_data={
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": None, "reasoning_content": "..."},
                    "finish_reason": "length",
                }
            ]
        }
    )

    assert client.complete([{"role": "user", "content": "hi"}], model="m") == ""


@pytest.mark.parametrize("status_code", [401, 403])
def test_auth_errors_propagate_via_raise_for_status(stub_requests, client, status_code):
    # Gateway error bodies are plain text, not JSON (§10) — no JSON parsing may occur.
    stub_requests.queue(status_code=status_code, text="authorization failed")

    with pytest.raises(requests.HTTPError):
        client.complete([{"role": "user", "content": "hi"}], model="m")
