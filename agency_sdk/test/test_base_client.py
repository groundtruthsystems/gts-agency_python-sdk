"""Wire-contract tests for the shared BaseDelegateClient (PR #5 M3).

All delegate clients now inherit this plumbing, so these offline tests pin the
exact request shape (URL = base_url + api_path + endpoint, bearer header, JSON
content type, body, params, timeout) and the empty-body handling.
"""

import pytest
import requests

from agency_sdk.delegates.base_client import BaseDelegateClient
from agency_sdk.test.conftest import StubResponse


class _Client(BaseDelegateClient):
    api_path = "/api/widgets"


@pytest.fixture
def client(fake_credentials):
    return _Client(token_supplier=fake_credentials, base_url="http://cp.test/")


def test_init_rstrips_base_url_and_binds_credentials(client, fake_credentials):
    assert client.base_url == "http://cp.test"
    assert client.token_supplier is fake_credentials


def test_make_request_composes_url_headers_and_body(stub_requests, client):
    stub_requests.queue(json_data={"ok": True})

    result = client._make_request("POST", "/42", data={"a": 1}, params={"o": "2"})

    assert result == {"ok": True}
    call = stub_requests.calls[0]
    assert call.method == "POST"
    assert call.url == "http://cp.test/api/widgets/42"
    assert call.kwargs["headers"]["Authorization"] == "Bearer test-token"
    assert call.kwargs["headers"]["Content-Type"] == "application/json"
    assert call.kwargs["json"] == {"a": 1}
    assert call.kwargs["params"] == {"o": "2"}
    assert call.kwargs["timeout"] == 30


def test_make_request_returns_empty_dict_on_no_content(stub_requests, client):
    stub_requests.queue(json_data=None)  # content == b"" -> {}

    assert client._make_request("DELETE", "/9") == {}


def test_request_without_json_content_type_omits_header(stub_requests, client):
    stub_requests.queue(text="raw-export-body")

    response = client._request("GET", "/export", params={"format": "turtle"}, json_content_type=False)

    assert response.text == "raw-export-body"
    call = stub_requests.calls[0]
    assert call.url == "http://cp.test/api/widgets/export"
    assert "Content-Type" not in call.kwargs["headers"]
    assert call.kwargs["headers"]["Authorization"] == "Bearer test-token"


class _FlakyTransport:
    """requests.request stand-in: raises `raises` on the first N calls, then succeeds."""

    def __init__(self, raises: Exception, fail_times: int, response: StubResponse):
        self.raises = raises
        self.fail_times = fail_times
        self.response = response
        self.calls = 0

    def __call__(self, **kwargs) -> StubResponse:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.raises
        return self.response


def test_connection_errors_are_retried_then_succeed(client, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("agency_sdk.delegates.base_client.time.sleep", lambda s: sleeps.append(s))
    transport = _FlakyTransport(
        requests.ConnectionError("blip"), fail_times=2, response=StubResponse(json_data={"ok": True})
    )
    monkeypatch.setattr(requests, "request", transport)

    result = client._make_request("GET", "/x")

    assert result == {"ok": True}
    assert transport.calls == 3  # 2 retries + success
    assert sleeps == [1.0, 2.0]  # exponential backoff between the 3 attempts


def test_connection_error_exhausts_retries_and_raises(client, monkeypatch):
    monkeypatch.setattr("agency_sdk.delegates.base_client.time.sleep", lambda s: None)
    transport = _FlakyTransport(requests.ConnectionError("down"), fail_times=99, response=StubResponse())
    monkeypatch.setattr(requests, "request", transport)

    with pytest.raises(requests.ConnectionError):
        client._make_request("GET", "/x")

    assert transport.calls == 3  # bounded: initial + 2 retries, then propagate


def test_read_timeout_is_not_retried(client, monkeypatch):
    # A slow server (read timeout) must NOT be retried — retrying would multiply the hang.
    monkeypatch.setattr("agency_sdk.delegates.base_client.time.sleep", lambda s: None)
    transport = _FlakyTransport(requests.ReadTimeout("slow"), fail_times=99, response=StubResponse())
    monkeypatch.setattr(requests, "request", transport)

    with pytest.raises(requests.ReadTimeout):
        client._make_request("GET", "/x")

    assert transport.calls == 1  # single attempt, no retry


def test_http_status_errors_are_not_retried(client, stub_requests):
    # 409 (and any 4xx/5xx) is raised by raise_for_status OUTSIDE the retry loop, so a
    # control-flow conflict is never silently re-sent.
    stub_requests.queue(json_data={"message": "conflict"}, status_code=409)

    with pytest.raises(requests.HTTPError):
        client._make_request("POST", "/x", data={"a": 1})

    assert len(stub_requests.calls) == 1


def test_post_is_not_auto_retried_on_connection_error(client, monkeypatch):
    # A ConnectionError can be a reset AFTER the server committed, so a non-idempotent
    # POST must be sent exactly once by default — retrying would double-apply it (e.g.
    # corrupt work_queue's 409-as-control-flow by re-hitting the UNIQUE key).
    monkeypatch.setattr("agency_sdk.delegates.base_client.time.sleep", lambda s: None)
    transport = _FlakyTransport(requests.ConnectionError("reset"), fail_times=99, response=StubResponse())
    monkeypatch.setattr(requests, "request", transport)

    with pytest.raises(requests.ConnectionError):
        client._make_request("POST", "/x", data={"a": 1})

    assert transport.calls == 1  # exactly once, no retry


def test_write_can_opt_into_retry(client, monkeypatch):
    # An idempotent write (e.g. session update) opts in explicitly with retry=True.
    monkeypatch.setattr("agency_sdk.delegates.base_client.time.sleep", lambda s: None)
    transport = _FlakyTransport(
        requests.ConnectionError("blip"), fail_times=2, response=StubResponse(json_data={"ok": True})
    )
    monkeypatch.setattr(requests, "request", transport)

    result = client._make_request("POST", "/x", data={"a": 1}, retry=True)

    assert result == {"ok": True}
    assert transport.calls == 3
