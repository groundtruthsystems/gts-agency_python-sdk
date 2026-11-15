"""Graphs delegate tests: knowledge-graph list and condition subgraph via comand's medical-ontology proxy.

Synthetic codes, bids and documents only. The canary token is not a real credential.
"""

import pytest
import requests
from pydantic import ValidationError

from agency_sdk.delegates import base_client
from agency_sdk.delegates.graphs_client import AgencyGraphsClient
from agency_sdk.delegates.graphs_dto import (
    ConditionClassMismatch,
    ConditionNotFound,
    ConditionSubgraph,
    KnowledgeGraphList,
)

BASE = "http://cp.test"
PROXY = f"{BASE}/api/knowledge-graphs/medical-ontology/api/models"
BID = "0f8fad5b-d9cb-469f-a165-70867728950e"
ORG = 2
CANARY = "CANARY-TOKEN-NOT-REAL"
MOS_NOT_FOUND = {"detail": "not found"}

ROOT = {"bid": BID, "class": "condition", "code": "C0000001", "name": "Synthetic condition"}
WALK = {
    "vertices": [ROOT, {"bid": "11111111-2222-3333-4444-555555555555", "class": "medication"}],
    "edges": [{"label": "treats", "from": "11111111-2222-3333-4444-555555555555", "to": BID}],
}
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


# --------------------------------------------------------------------------- condition_subgraph (UC-02)


def test_subgraph_cui_found(stub_requests, client):
    """TS-05."""
    stub_requests.queue(ROOT)
    stub_requests.queue({"vertices": WALK["vertices"], "edges": WALK["edges"]})

    result = client.condition_subgraph(ORG, "C0000001")

    assert isinstance(result, ConditionSubgraph)
    assert result.root == ROOT
    assert result.vertices == WALK["vertices"]
    assert result.edges == WALK["edges"]


def test_subgraph_cui_wire(stub_requests, client):
    """TS-06."""
    stub_requests.queue(ROOT)
    stub_requests.queue(WALK)

    client.condition_subgraph(ORG, "C0000001")

    resolve, walk = stub_requests.calls
    assert resolve.method == "GET"
    assert resolve.url == f"{PROXY}/_/C0000001"
    assert resolve.kwargs["params"] == {"o": str(ORG), "source": "code"}
    assert walk.method == "GET"
    assert walk.url == f"{PROXY}/condition/{BID}/_graph"
    assert walk.kwargs["params"] == {"o": str(ORG), "depth": "1", "direction": "any"}
    assert resolve.kwargs["headers"]["Authorization"] == "Bearer test-token"
    assert walk.kwargs["headers"]["Authorization"] == "Bearer test-token"
    assert resolve.kwargs["timeout"] == 30 and walk.kwargs["timeout"] == 30


def test_subgraph_b_code_wire(stub_requests, client):
    """TS-07: authored B codes resolve through the same source=code path."""
    stub_requests.queue(dict(ROOT, code="B0000001"))
    stub_requests.queue(WALK)

    result = client.condition_subgraph(ORG, "B0000001")

    resolve, walk = stub_requests.calls
    assert resolve.url == f"{PROXY}/_/B0000001"
    assert resolve.kwargs["params"] == {"o": str(ORG), "source": "code"}
    assert walk.url == f"{PROXY}/condition/{BID}/_graph"
    assert result.root["code"] == "B0000001"


def test_subgraph_depth(stub_requests, client):
    """TS-08 (valid)."""
    stub_requests.queue(ROOT)
    stub_requests.queue(WALK)

    client.condition_subgraph(ORG, "C0000001", depth=3, direction="in")

    assert stub_requests.calls[1].kwargs["params"] == {"o": str(ORG), "depth": "3", "direction": "in"}


@pytest.mark.parametrize(
    "kwargs", [{"depth": 0}, {"depth": 7}, {"depth": "2"}, {"depth": True}, {"depth": 2.0}, {"direction": "up"}]
)
def test_subgraph_depth_direction_invalid(stub_requests, client, kwargs):
    """TS-08 (invalid): ValueError, zero requests."""
    with pytest.raises(ValueError):
        client.condition_subgraph(ORG, "C0000001", **kwargs)
    assert stub_requests.calls == []


def test_subgraph_not_found(stub_requests, client):
    """TS-09."""
    stub_requests.queue(MOS_NOT_FOUND, status_code=404)

    with pytest.raises(ConditionNotFound) as info:
        client.condition_subgraph(ORG, "C0000001")

    assert isinstance(info.value, requests.HTTPError)
    assert info.value.response.status_code == 404
    assert "C0000001" not in str(info.value)
    assert len(stub_requests.calls) == 1


@pytest.mark.parametrize("status", [401, 403, 500, 502])
def test_subgraph_resolve_errors_not_not_found(stub_requests, client, status):
    """TS-10."""
    stub_requests.queue(MOS_NOT_FOUND, status_code=status)

    with pytest.raises(requests.HTTPError) as info:
        client.condition_subgraph(ORG, "C0000001")
    assert not isinstance(info.value, ConditionNotFound)
    assert len(stub_requests.calls) == 1


@pytest.mark.parametrize("status", [404, 500])
def test_subgraph_walk_errors(stub_requests, client, status):
    """TS-11: a walk failure is never 'not found', even a MOS-shaped 404."""
    stub_requests.queue(ROOT)
    stub_requests.queue(MOS_NOT_FOUND, status_code=status)

    with pytest.raises(requests.HTTPError) as info:
        client.condition_subgraph(ORG, "C0000001")
    assert not isinstance(info.value, ConditionNotFound)


@pytest.mark.parametrize("error, per_step_calls", [(requests.ConnectionError, 3), (requests.ReadTimeout, 1)])
@pytest.mark.parametrize("failing_step", ["resolve", "walk"])
def test_subgraph_transport_errors(monkeypatch, no_sleep, client, error, per_step_calls, failing_step):
    """TS-12."""
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((url, kwargs["timeout"]))
        if failing_step == "resolve" or url.endswith("/_graph"):
            raise error("down")
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"bid": "0f8fad5b-d9cb-469f-a165-70867728950e", "class": "condition"}'  # noqa: SLF001
        return response

    monkeypatch.setattr(requests, "request", fake_request)
    with pytest.raises(error) as info:
        client.condition_subgraph(ORG, "C0000001")

    assert not isinstance(info.value, ConditionNotFound)
    expected = per_step_calls if failing_step == "resolve" else 1 + per_step_calls
    assert len(calls) == expected
    assert all(timeout == 30 for _url, timeout in calls)


@pytest.mark.parametrize(
    "code",
    [
        "",
        "X1234567",
        "C123/../",
        "C1234567\n",
        "C123456?",
        "C12%2F45",
        "c1234567",
        "C123456",
        "C12345678",
        None,
        1234567,
    ],
)
def test_subgraph_invalid_code(stub_requests, client, code):
    """TS-13: ValueError, zero requests, message omits the input."""
    with pytest.raises(ValueError) as info:
        client.condition_subgraph(ORG, code)
    assert stub_requests.calls == []
    message = str(info.value)
    if isinstance(code, str) and code:
        assert code.strip() not in message
    assert "C" in message and "B" in message


@pytest.mark.parametrize("bid", ["x/../y", "B0000001", BID + "\n", "", 42])
def test_subgraph_rejects_unsafe_server_bid(stub_requests, client, bid):
    """TS-14."""
    stub_requests.queue({"bid": bid, "class": "condition"})

    with pytest.raises(ValidationError):
        client.condition_subgraph(ORG, "C0000001")
    assert len(stub_requests.calls) == 1


def test_subgraph_class_mismatch(stub_requests, client):
    """TS-15."""
    stub_requests.queue({"bid": BID, "class": "medication"})

    with pytest.raises(ConditionClassMismatch) as info:
        client.condition_subgraph(ORG, "C0000001")
    assert info.value.entity_class == "medication"
    assert len(stub_requests.calls) == 1


@pytest.mark.parametrize(
    "resolve_kwargs",
    [
        {"json_data": {"class": "condition"}},
        {"json_data": {"bid": BID}},
        {"json_data": []},
        {"json_data": None, "content_bytes": b"null"},
    ],
)
def test_subgraph_malformed_resolve(stub_requests, client, resolve_kwargs):
    """TS-16 (resolve): ValidationError, zero walk requests."""
    stub_requests.queue(**resolve_kwargs)

    with pytest.raises(ValidationError):
        client.condition_subgraph(ORG, "C0000001")
    assert len(stub_requests.calls) == 1


@pytest.mark.parametrize("walk", [{"edges": []}, {"vertices": [], "edges": {}}, [], None])
def test_subgraph_malformed_walk(stub_requests, client, walk):
    """TS-16 (walk): ValidationError, nothing returned."""
    stub_requests.queue(ROOT)
    if walk is None:
        stub_requests.queue(None, content_bytes=b"null")
    else:
        stub_requests.queue(walk)

    with pytest.raises(ValidationError):
        client.condition_subgraph(ORG, "C0000001")


def test_subgraph_empty_edges_is_found(stub_requests, client):
    """TS-17: an empty walk is a found condition; legacy edges without evidence keys parse."""
    stub_requests.queue(ROOT)
    stub_requests.queue({"vertices": [], "edges": []})
    assert client.condition_subgraph(ORG, "C0000001").edges == []

    stub_requests.queue(ROOT)
    stub_requests.queue({"vertices": [ROOT], "edges": [{"label": "has_symptom"}]})
    assert client.condition_subgraph(ORG, "C0000001").edges == [{"label": "has_symptom"}]


@pytest.mark.parametrize(
    "queue_kwargs",
    [
        {"json_data": None},
        {"json_data": None, "content_bytes": b"<html>Not Found</html>", "text": "<html>Not Found</html>"},
        {"json_data": {"error": {"code": "NOT_FOUND"}}},
        {"json_data": {"detail": "other"}},
        {"json_data": {"detail": "not found", "extra": 1}},
    ],
)
def test_subgraph_non_mos_404_is_error(stub_requests, client, queue_kwargs):
    """TS-32: a misrouted 404 (comand/gateway) is a plain HTTPError, never 'not found'."""
    stub_requests.queue(status_code=404, **queue_kwargs)

    with pytest.raises(requests.HTTPError) as info:
        client.condition_subgraph(ORG, "C0000001")
    assert not isinstance(info.value, ConditionNotFound)


def test_subgraph_non_json_404_with_real_response_is_error(monkeypatch, client):
    """TS-32: a real requests.Response whose 404 body is not JSON is a plain HTTPError."""

    def fake_request(method, url, **kwargs):
        response = requests.Response()
        response.status_code = 404
        response._content = b"<html>nope</html>"  # noqa: SLF001
        response.url = url
        return response

    monkeypatch.setattr(requests, "request", fake_request)
    with pytest.raises(requests.HTTPError) as info:
        client.condition_subgraph(ORG, "C0000001")
    assert not isinstance(info.value, ConditionNotFound)


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


ROOT_BYTES = b'{"bid": "0f8fad5b-d9cb-469f-a165-70867728950e", "class": "condition"}'
FAILING_PLANS = [
    ("list", [(401, b'{"error": "unauthorised"}')]),
    ("list", [(200, b"[]")]),
    ("subgraph", [(404, b'{"detail": "not found"}')]),
    ("subgraph", [(404, b"<html></html>")]),
    ("subgraph", [(500, b"{}")]),
    ("subgraph", [(200, b'{"bid": "0f8fad5b-d9cb-469f-a165-70867728950e", "class": "medication"}')]),
    ("subgraph", [(200, b'{"bid": "x/../y", "class": "condition"}')]),
    ("subgraph", [(200, ROOT_BYTES), (502, b"{}")]),
    ("subgraph", [(200, ROOT_BYTES), (200, b'{"vertices": []}')]),
]


@pytest.mark.parametrize("call, plan", FAILING_PLANS)
def test_canary_token_absent(monkeypatch, call, plan):
    """TS-18: the bearer never appears in an exception's str/repr/args on any failing path."""
    fake_request, _sent = _real_response_factory(list(plan))
    monkeypatch.setattr(requests, "request", fake_request)
    graphs = AgencyGraphsClient(token_supplier=CanaryCredentials(), base_url=BASE)

    with pytest.raises(Exception) as info:
        if call == "list":
            graphs.list(ORG)
        else:
            graphs.condition_subgraph(ORG, "C0000001")

    exc = info.value
    for text in (str(exc), repr(exc), repr(exc.args)):
        assert CANARY not in text


def test_token_only_in_header(monkeypatch):
    """TS-19: on success the token travels only in the Authorization header and never reaches a DTO."""
    plan = [
        (200, b'{"items": [{"code": "medical-ontology", "name": "MO", "description": "", "enabled": true}]}'),
        (200, ROOT_BYTES),
        (200, b'{"vertices": [], "edges": []}'),
    ]
    fake_request, sent = _real_response_factory(plan)
    monkeypatch.setattr(requests, "request", fake_request)
    graphs = AgencyGraphsClient(token_supplier=CanaryCredentials(), base_url=BASE)

    kg = graphs.list(ORG)
    sg = graphs.condition_subgraph(ORG, "C0000001")

    for kwargs in sent:
        assert kwargs["headers"]["Authorization"] == f"Bearer {CANARY}"
        assert CANARY not in repr(kwargs["params"])
        assert kwargs.get("json") is None
    for dto in (kg, sg):
        assert CANARY not in repr(dto)
        assert CANARY not in repr(dto.model_dump())
