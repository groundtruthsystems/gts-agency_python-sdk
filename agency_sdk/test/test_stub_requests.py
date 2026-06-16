"""Sanity tests for the shared offline requests stub used by all client tests."""

import pytest
import requests


def test_recorder_captures_call_and_replays_queued_response(stub_requests):
    stub_requests.queue(json_data={"hello": "world"})

    response = requests.request(method="GET", url="http://example.test/api/x", params={"o": "2"})

    assert response.json() == {"hello": "world"}
    assert len(stub_requests.calls) == 1
    call = stub_requests.calls[0]
    assert call.method == "GET"
    assert call.url == "http://example.test/api/x"
    assert call.kwargs["params"] == {"o": "2"}


def test_stub_response_raises_http_error_for_4xx(stub_requests):
    stub_requests.queue(json_data={"error": "not found"}, status_code=404)

    response = requests.request(method="GET", url="http://example.test/api/missing")
    with pytest.raises(requests.HTTPError):
        response.raise_for_status()


def test_recorder_intercepts_requests_get_for_downloads(stub_requests):
    stub_requests.queue(content_bytes=b"abc123")

    response = requests.get("http://signed.example.test/blob", timeout=300, stream=True)

    assert b"".join(response.iter_content(chunk_size=2)) == b"abc123"
    assert stub_requests.calls[0].method == "GET"
    assert stub_requests.calls[0].kwargs["stream"] is True


def test_fake_credentials_supplies_static_token(fake_credentials):
    assert fake_credentials.bearer_token() == "test-token"
