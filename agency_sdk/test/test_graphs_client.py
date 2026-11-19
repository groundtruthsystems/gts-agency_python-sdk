"""Graphs delegate tests: the knowledge-graph list.

Synthetic data only. The canary token is not a real credential.
"""

import inspect

import pytest
import requests
from pydantic import ValidationError

from agency_sdk.delegates import base_client
from agency_sdk.delegates.graphs_client import AgencyGraphsClient
from agency_sdk.delegates.graphs_dto import KnowledgeGraphList
from agency_sdk.domain import Page
from agency_sdk.test.kg_paging_sim import INVALID_PAGE_BODY, entry, serve, walk

BASE = "http://cp.test"
ORG = 2
CANARY = "CANARY-TOKEN-NOT-REAL"

KG_ITEMS = [
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
KG_BODY = {"page": {"page": 0, "size": 10, "total": 2}, "items": KG_ITEMS}
CODES_25 = [f"kg-{i:03d}" for i in range(1, 26)]


@pytest.fixture
def client(fake_credentials):
    return AgencyGraphsClient(token_supplier=fake_credentials, base_url=BASE)


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(base_client.time, "sleep", lambda _s: None)


# --------------------------------------------------------------------------- list (UC-01)


def test_list_returns_items(stub_requests, client):
    """TS-01 (parse): the default page metadata and items are parsed."""
    stub_requests.queue(KG_BODY)

    result = client.list(ORG)

    assert isinstance(result, KnowledgeGraphList)
    assert isinstance(result.page, Page)
    assert (result.page.page, result.page.size, result.page.total) == (0, 10, 2)
    assert [e.code for e in result.items] == ["medical-ontology", "other"]
    assert result.items[1].config is None and result.items[1].credential_variable_id is None


def test_list_wire(stub_requests, client):
    """TS-01, TS-28: no page/size sends only ``o`` (server applies its defaults; no client ``s``)."""
    stub_requests.queue(KG_BODY)

    client.list(ORG)

    (call,) = stub_requests.calls
    assert call.method == "GET"
    assert call.url == f"{BASE}/api/knowledge-graphs"
    assert call.kwargs["params"] == {"o": str(ORG)}
    assert call.kwargs["headers"]["Authorization"] == "Bearer test-token"


@pytest.mark.parametrize("status", [401, 403, 500])
@pytest.mark.parametrize("paging", [{}, {"page": 1, "size": 5}])
def test_list_errors_raise(stub_requests, client, status, paging):
    """TS-29 (HTTP statuses), with and without paging args."""
    stub_requests.queue({"error": "x"}, status_code=status)

    with pytest.raises(requests.HTTPError) as info:
        client.list(ORG, **paging)
    assert info.value.response.status_code == status
    assert len(stub_requests.calls) == 1


@pytest.mark.parametrize("error, expected_calls", [(requests.ConnectionError, 3), (requests.ReadTimeout, 1)])
def test_list_transport_errors_raise(monkeypatch, no_sleep, client, error, expected_calls):
    """TS-29 (transport): connection errors retry to 3 attempts, read timeouts never retry."""
    calls = []

    def boom(method, url, **kwargs):
        calls.append(url)
        raise error("down")

    monkeypatch.setattr(requests, "request", boom)
    with pytest.raises(error):
        client.list(ORG, page=2, size=3)
    assert len(calls) == expected_calls


@pytest.mark.parametrize(
    "queue_kwargs",
    [
        {"json_data": []},
        {"json_data": None, "content_bytes": b"null"},
        {"json_data": {}},
        {"json_data": {"items": []}},
        {"json_data": {"items": KG_ITEMS}},
        {"json_data": {"page": {"page": 0, "size": 10, "total": 0}, "items": {}}},
        {"json_data": {"page": {"page": 0, "size": 10, "total": "many"}, "items": []}},
        {"json_data": {"page": {"page": 0, "total": 0}, "items": []}},
        {"json_data": {"page": None, "items": []}},
        {"json_data": {"page": {"page": 0, "size": 10, "total": 1}, "items": [{"name": "x"}]}},
    ],
)
def test_list_malformed_raises(stub_requests, client, queue_kwargs):
    """TS-13, TS-14: an old ``{items}`` body (no ``page``) or a malformed body fails validation."""
    stub_requests.queue(**queue_kwargs)

    with pytest.raises(ValidationError):
        client.list(ORG)


# --------------------------------------------------------------------------- paging (UC-02, UC-03)


def test_list_explicit_page_and_size(stub_requests, client):
    """TS-02: page=1, size=5 is sent as p/s and the metadata is parsed."""
    stub_requests.queue({"page": {"page": 1, "size": 5, "total": 7}, "items": [entry("kg-006"), entry("kg-007")]})

    result = client.list(ORG, page=1, size=5)

    assert stub_requests.calls[0].kwargs["params"] == {"o": str(ORG), "p": "1", "s": "5"}
    assert (result.page.page, result.page.size, result.page.total) == (1, 5, 7)


@pytest.mark.parametrize(
    "paging, expected",
    [({"page": 2}, {"o": str(ORG), "p": "2"}), ({"size": 3}, {"o": str(ORG), "s": "3"})],
)
def test_list_sends_only_given_param(stub_requests, client, paging, expected):
    """TS-03: an omitted page or size is absent from the request, never defaulted by the client."""
    stub_requests.queue(KG_BODY)

    client.list(ORG, **paging)

    assert stub_requests.calls[0].kwargs["params"] == expected


def test_list_sends_zero_values(stub_requests, client):
    """TS-04: zero is sent (``is not None``), not dropped as falsy."""
    stub_requests.queue({"page": {"page": 0, "size": 0, "total": 2}, "items": []})

    client.list(ORG, page=0, size=0)

    assert stub_requests.calls[0].kwargs["params"] == {"o": str(ORG), "p": "0", "s": "0"}


def test_list_page_and_size_are_keyword_only(stub_requests, client):
    """TS-05: page/size are keyword-only, default None; a positional page is a TypeError."""
    params = inspect.signature(AgencyGraphsClient.list).parameters
    for name in ("page", "size"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert params[name].default is None

    with pytest.raises(TypeError):
        client.list(ORG, 1)  # type: ignore[misc]
    assert stub_requests.calls == []


def test_list_last_partial_page(monkeypatch, client):
    """TS-06: total 12, size 5, page 2 returns the 2 remaining items."""
    serve(monkeypatch, [entry(c) for c in CODES_25[:12]])

    result = client.list(ORG, page=2, size=5)

    assert [e.code for e in result.items] == ["kg-011", "kg-012"]
    assert result.page.total == 12


def test_list_page_past_end(monkeypatch, client):
    """TS-07: a page past the end is an empty page carrying the real total."""
    serve(monkeypatch, [entry(c) for c in CODES_25[:12]])

    result = client.list(ORG, page=9, size=5)

    assert result.items == []
    assert (result.page.page, result.page.total) == (9, 12)


def test_list_size_zero(monkeypatch, client):
    """TS-08: size=0 is sent and returns an empty page with page.size == 0."""
    server = serve(monkeypatch, [entry(c) for c in CODES_25[:3]])

    result = client.list(ORG, size=0)

    assert result.items == []
    assert (result.page.size, result.page.total) == (0, 3)
    assert server.calls[0]["params"]["s"] == "0"


@pytest.mark.parametrize("paging, sent", [({"page": -1}, ("p", "-1")), ({"size": -1}, ("s", "-1"))])
def test_list_negative_values_reach_server(monkeypatch, client, paging, sent):
    """TS-09, TS-10: negatives are not rejected client-side; the server's 500 surfaces as HTTPError."""
    server = serve(monkeypatch, [entry("kg-001")])

    with pytest.raises(requests.HTTPError) as info:
        client.list(ORG, **paging)

    assert info.value.response.status_code == 500
    assert info.value.response.json() == INVALID_PAGE_BODY
    (call,) = server.calls
    assert call["params"][sent[0]] == sent[1]


def test_list_large_size_not_clamped(monkeypatch, client):
    """TS-10, TS-28: a very large size passes through unchanged (no client maximum)."""
    server = serve(monkeypatch, [entry(c) for c in CODES_25])

    result = client.list(ORG, size=10**9)

    assert server.calls[0]["params"]["s"] == str(10**9)
    assert len(result.items) == 25


def test_walk_collects_every_code_once(monkeypatch, client):
    """TS-11: walking pages by page.total collects every code exactly once, in code order."""
    serve(monkeypatch, [entry(c) for c in reversed(CODES_25)])

    assert walk(client, ORG, size=4) == CODES_25


def test_walk_size_zero_stops_on_empty_page(monkeypatch, client):
    """TS-12: a size=0 walk stops on the first (empty) page instead of looping forever."""
    server = serve(monkeypatch, [entry(c) for c in CODES_25[:3]])

    assert walk(client, ORG, size=0) == []
    assert len(server.calls) == 1


def test_empty_catalogue(monkeypatch, client):
    """TS-32 (client): an empty catalogue is an empty page with total 0; a walk makes one request."""
    server = serve(monkeypatch, [])

    result = client.list(ORG)
    assert result.items == [] and result.page.total == 0
    assert walk(client, ORG) == []
    assert len(server.calls) == 2


@pytest.mark.parametrize("paging", [{}, {"page": 0}, {"size": 5}, {"page": 3, "size": 2}, {"page": -1, "size": -1}])
def test_org_always_sent(monkeypatch, client, paging):
    """TS-27: ``o`` is sent for every page/size combination and paging never adds another org param."""
    server = serve(monkeypatch, [entry("kg-001")])

    try:
        client.list(ORG, **paging)
    except requests.HTTPError:
        pass

    params = server.calls[0]["params"]
    assert params["o"] == str(ORG)
    assert set(params) <= {"o", "p", "s"}


def test_no_verify_override(monkeypatch, client):
    """TS-33: list() never passes a verify override to requests."""
    server = serve(monkeypatch, [entry("kg-001")])

    client.list(ORG, page=0, size=1)

    assert "verify" not in server.calls[0]


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
    [(500, b'{"error": {"type": "SERVICE_ERROR", "req_uuid": "r", "message": "Invalid page request"}}')],
    [(200, b'{"items": []}')],
]


@pytest.mark.parametrize("plan", FAILING_PLANS)
def test_canary_token_absent(monkeypatch, plan):
    """TS-26 (was TS-18): the bearer never appears in an exception's str/repr/args on any failing path."""
    fake_request, _sent = _real_response_factory(list(plan))
    monkeypatch.setattr(requests, "request", fake_request)
    graphs = AgencyGraphsClient(token_supplier=CanaryCredentials(), base_url=BASE)

    with pytest.raises(Exception) as info:
        graphs.list(ORG, page=-1, size=5)

    exc = info.value
    for text in (str(exc), repr(exc), repr(exc.args)):
        assert CANARY not in text


def test_token_only_in_header(monkeypatch):
    """TS-19: on success the token travels only in the Authorization header and never reaches a DTO."""
    plan = [
        (
            200,
            b'{"page": {"page": 1, "size": 1, "total": 2},'
            b' "items": [{"code": "medical-ontology", "name": "MO", "description": "", "enabled": true}]}',
        ),
    ]
    fake_request, sent = _real_response_factory(plan)
    monkeypatch.setattr(requests, "request", fake_request)
    graphs = AgencyGraphsClient(token_supplier=CanaryCredentials(), base_url=BASE)

    kg = graphs.list(ORG, page=1, size=1)

    for kwargs in sent:
        assert kwargs["headers"]["Authorization"] == f"Bearer {CANARY}"
        assert CANARY not in repr(kwargs["params"])
        assert kwargs.get("json") is None
    assert CANARY not in repr(kg)
    assert CANARY not in repr(kg.model_dump())
