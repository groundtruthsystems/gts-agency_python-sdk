"""Graphs delegate tests: the knowledge-graph list.

Synthetic data only. The canary token is not a real credential.
"""

import pytest
import requests
from pydantic import ValidationError

from agency_sdk.delegates import base_client
from agency_sdk.delegates.graphs_client import AgencyGraphsClient
from agency_sdk.delegates.graphs_dto import KnowledgeGraphList

BASE = "http://cp.test"
ORG = 2
CANARY = "CANARY-TOKEN-NOT-REAL"

KG_BODY = {
    "items": [
        {
            "code": "medical-ontology",
            "name": "Medical Ontology",
            "description": "Built-in",
            "enabled": True,
            "credential_variable_id": "var-1",
            "config": {"base_url": "https://example.invalid"},
        },
        {"code": "other", "name": "Other", "description": "", "enabled": False},
    ]
}


@pytest.fixture
def client(fake_credentials):
    return AgencyGraphsClient(token_supplier=fake_credentials, base_url=BASE)


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(base_client.time, "sleep", lambda _s: None)


# --------------------------------------------------------------------------- list (UC-01)


def test_list_returns_items(stub_requests, client):
    """TS-01."""
    stub_requests.queue(KG_BODY)

    result = client.list(ORG)

    assert isinstance(result, KnowledgeGraphList)
    assert [e.code for e in result.items] == ["medical-ontology", "other"]
    assert result.items[1].config is None and result.items[1].credential_variable_id is None


def test_list_wire(stub_requests, client):
    """TS-02."""
    stub_requests.queue(KG_BODY)

    client.list(ORG)

    (call,) = stub_requests.calls
    assert call.method == "GET"
    assert call.url == f"{BASE}/api/knowledge-graphs"
    assert call.kwargs["params"] == {"o": str(ORG)}
    assert call.kwargs["headers"]["Authorization"] == "Bearer test-token"


@pytest.mark.parametrize("status", [401, 403, 500])
def test_list_errors_raise(stub_requests, client, status):
    """TS-03 (HTTP statuses)."""
    stub_requests.queue({"error": "x"}, status_code=status)

    with pytest.raises(requests.HTTPError) as info:
        client.list(ORG)
    assert info.value.response.status_code == status
    assert len(stub_requests.calls) == 1


@pytest.mark.parametrize("error, expected_calls", [(requests.ConnectionError, 3), (requests.ReadTimeout, 1)])
def test_list_transport_errors_raise(monkeypatch, no_sleep, client, error, expected_calls):
    """TS-03 (transport): connection errors retry to 3 attempts, read timeouts never retry."""
    calls = []

    def boom(method, url, **kwargs):
        calls.append(url)
        raise error("down")

    monkeypatch.setattr(requests, "request", boom)
    with pytest.raises(error):
        client.list(ORG)
    assert len(calls) == expected_calls


@pytest.mark.parametrize(
    "queue_kwargs",
    [
        {"json_data": []},
        {"json_data": None, "content_bytes": b"null"},
        {"json_data": {}},
        {"json_data": {"items": {}}},
        {"json_data": {"items": [{"name": "x", "description": "", "enabled": True}]}},
    ],
)
def test_list_malformed_raises(stub_requests, client, queue_kwargs):
    """TS-04."""
    stub_requests.queue(**queue_kwargs)

    with pytest.raises(ValidationError):
        client.list(ORG)


# --------------------------------------------------------------------------- token secrecy (NFR-01, TM-1)


class CanaryCredentials:
    def bearer_token(self) -> str:
        return CANARY


def _real_response_factory(plan):
    """Build real requests.Response objects (with a PreparedRequest) from a list of (status, body bytes)."""
    sent = []

    def fake_request(method, url, **kwargs):
        sent.append(kwargs)
        status, body = plan.pop(0)
        prepared = requests.Request(method, url, headers=kwargs["headers"], params=kwargs["params"]).prepare()
        response = requests.Response()
        response.status_code = status
        response._content = body  # noqa: SLF001
        response.url = prepared.url
        response.request = prepared
        response.reason = "X"
        return response

    return fake_request, sent


FAILING_PLANS = [
    [(401, b'{"error": "unauthorised"}')],
    [(500, b"{}")],
    [(200, b"[]")],
]


@pytest.mark.parametrize("plan", FAILING_PLANS)
def test_canary_token_absent(monkeypatch, plan):
    """TS-18: the bearer never appears in an exception's str/repr/args on any failing path."""
    fake_request, _sent = _real_response_factory(list(plan))
    monkeypatch.setattr(requests, "request", fake_request)
    graphs = AgencyGraphsClient(token_supplier=CanaryCredentials(), base_url=BASE)

    with pytest.raises(Exception) as info:
        graphs.list(ORG)

    exc = info.value
    for text in (str(exc), repr(exc), repr(exc.args)):
        assert CANARY not in text


def test_token_only_in_header(monkeypatch):
    """TS-19: on success the token travels only in the Authorization header and never reaches a DTO."""
    plan = [
        (200, b'{"items": [{"code": "medical-ontology", "name": "MO", "description": "", "enabled": true}]}'),
    ]
    fake_request, sent = _real_response_factory(plan)
    monkeypatch.setattr(requests, "request", fake_request)
    graphs = AgencyGraphsClient(token_supplier=CanaryCredentials(), base_url=BASE)

    kg = graphs.list(ORG)

    for kwargs in sent:
        assert kwargs["headers"]["Authorization"] == f"Bearer {CANARY}"
        assert CANARY not in repr(kwargs["params"])
        assert kwargs.get("json") is None
    assert CANARY not in repr(kg)
    assert CANARY not in repr(kg.model_dump())
