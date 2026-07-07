"""Offline tests for native SSE streaming in AgencyGatewayClient.

Pins the OpenAI-compatible streaming contract as proxied by the gateway
(verified live 2026-07-07 via the openai SDK): `data: {json}` events, an
optional usage-only final chunk with empty `choices`, terminated by
`data: [DONE]`. Also pins the defect fix: the non-streaming methods must
fail fast with ValueError when `stream` is passed (previously they blocked
buffering the whole SSE body, then died with an unrelated JSONDecodeError).
"""

import pytest

from agency_sdk.delegates.gateway_client import AgencyGatewayClient
from agency_sdk.delegates.gateway_dto import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatMessage,
)

SSE_BODY = b"""data: {"choices":[{"index":0,"delta":{"role":"assistant","content":""}}]}

data: {"choices":[{"index":0,"delta":{"content":"po"}}]}

: keep-alive comment, must be ignored

data: {"choices":[{"index":0,"delta":{"content":"ng"}}]}

data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: {"choices":[],"usage":{"total_tokens":10}}

data: [DONE]

data: {"choices":[{"index":0,"delta":{"content":"MUST NOT APPEAR"}}]}
"""


@pytest.fixture
def client(fake_credentials):
    return AgencyGatewayClient(
        token_supplier=fake_credentials,
        gateway_base_url="http://gw.test:4000",
        org_id="2",
    )


def _request() -> ChatCompletionRequest:
    return ChatCompletionRequest(model="biglambda1", messages=[ChatMessage(role="user", content="hi")])


def test_chunk_dto_parses_delta_and_usage_only_shapes():
    chunk = ChatCompletionChunk(**{"choices": [{"index": 0, "delta": {"content": "po"}}]})
    assert chunk.choices[0].delta.content == "po"
    assert chunk.choices[0].delta.role is None
    assert chunk.choices[0].finish_reason is None

    final = ChatCompletionChunk(**{"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    assert final.choices[0].delta.content is None
    assert final.choices[0].finish_reason == "stop"

    usage_only = ChatCompletionChunk(**{"choices": [], "usage": {"total_tokens": 10}})
    assert usage_only.choices == []  # tolerated, not an error


def test_chat_completions_stream_posts_sse_and_parses_chunks(stub_requests, client):
    response = stub_requests.queue(content_bytes=SSE_BODY)

    chunks = list(client.chat_completions_stream(_request()))

    call = stub_requests.calls[0]
    assert call.method == "POST"
    assert call.url == "http://gw.test:4000/v1/chat/completions"
    assert call.kwargs["json"]["stream"] is True  # forced into the body
    assert call.kwargs["stream"] is True  # requests-level streaming, no buffering
    assert call.kwargs["headers"]["x-org"] == "2"
    assert call.kwargs["timeout"] == 120

    assert len(chunks) == 5  # every data event before [DONE]; nothing after it
    assert chunks[1].choices[0].delta.content == "po"
    assert chunks[3].choices[0].finish_reason == "stop"
    assert chunks[4].choices == []  # usage-only final chunk
    assert response.closed  # response released once the stream is exhausted


def test_complete_stream_yields_only_nonempty_content_deltas(stub_requests, client):
    stub_requests.queue(content_bytes=SSE_BODY)

    deltas = list(client.complete_stream([{"role": "user", "content": "hi"}], model="biglambda1", temperature=0.0))

    assert deltas == ["po", "ng"]  # empty-string and delta-less chunks skipped
    assert stub_requests.calls[0].kwargs["json"]["temperature"] == 0.0  # kwargs pass through


def test_stream_handles_multibyte_utf8_content(stub_requests, client):
    # \xe2\x9c\x85 = '✅'. Its 0x85 byte is NEL in latin-1; requests defaults
    # text/event-stream (no charset) to ISO-8859-1, so the naive
    # decode_unicode+splitlines path split the JSON line mid-string and
    # mojibake'd the text (found live 2026-07-07). Byte mode + explicit UTF-8
    # must yield the emoji intact.
    stub_requests.queue(
        content_bytes=(b'data: {"choices":[{"index":0,"delta":{"content":"\xe2\x9c\x85 done"}}]}\n\ndata: [DONE]\n\n')
    )

    deltas = list(client.complete_stream([{"role": "user", "content": "hi"}], model="m"))

    assert deltas == ["✅ done"]


def test_stream_early_exit_closes_response(stub_requests, client):
    response = stub_requests.queue(content_bytes=SSE_BODY)

    stream = client.chat_completions_stream(_request())
    next(stream)  # consume one chunk only
    stream.close()  # caller abandons the stream

    assert response.closed


def test_stream_http_error_propagates_and_closes_response(stub_requests, client):
    response = stub_requests.queue(status_code=401, text="authentication failure")

    with pytest.raises(Exception) as excinfo:
        next(client.chat_completions_stream(_request()))
    assert "401" in str(excinfo.value)
    assert getattr(response, "closed", False)  # error responses must be closed too, not left to GC


def test_complete_stream_yields_only_first_choice_with_n_gt_1(stub_requests, client):
    # With n>1 passed through, servers stream each choice's deltas as separate
    # chunks carrying an explicit index; only index 0 may reach the caller.
    stub_requests.queue(
        content_bytes=(
            b'data: {"choices":[{"index":0,"delta":{"content":"A1"}}]}\n\n'
            b'data: {"choices":[{"index":1,"delta":{"content":"B1"}}]}\n\n'
            b'data: {"choices":[{"index":0,"delta":{"content":"A2"}}]}\n\n'
            b'data: {"choices":[{"index":1,"delta":{"content":"B2"}}]}\n\n'
            b"data: [DONE]\n\n"
        )
    )

    deltas = list(client.complete_stream([{"role": "user", "content": "hi"}], model="m", n=2))

    assert deltas == ["A1", "A2"]  # choice-1 deltas must not interleave into the text


def test_chat_completions_rejects_stream_before_any_network_call(stub_requests, client):
    request = ChatCompletionRequest(model="biglambda1", messages=[ChatMessage(role="user", content="hi")], stream=True)

    with pytest.raises(ValueError, match="stream"):
        client.chat_completions(request)
    with pytest.raises(ValueError, match="stream"):
        client.complete([{"role": "user", "content": "hi"}], model="biglambda1", stream=True)

    assert stub_requests.calls == []  # fails fast; previously blocked then JSONDecodeError
