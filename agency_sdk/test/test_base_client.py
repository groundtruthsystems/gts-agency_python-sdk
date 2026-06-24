"""Wire-contract tests for the shared BaseDelegateClient (PR #5 M3).

All delegate clients now inherit this plumbing, so these offline tests pin the
exact request shape (URL = base_url + api_path + endpoint, bearer header, JSON
content type, body, params, timeout) and the empty-body handling.
"""

import pytest

from agency_sdk.delegates.base_client import BaseDelegateClient


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
