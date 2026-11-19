"""Parity tests: ``FakeGraphs`` must match the real graphs delegate's signature and paging behaviour.

The real client runs against ``kg_paging_sim``, an offline stand-in for comand's paged endpoint (cf1d53a3).
Synthetic data only.
"""

import inspect

import pytest
import requests

from agency_sdk.delegates import datasets_dto
from agency_sdk.delegates.graphs_client import AgencyGraphsClient
from agency_sdk.delegates.graphs_dto import KnowledgeGraphList
from agency_sdk.domain import Page
from agency_sdk.test.adk_fakes import FakeControlPlane, FakeGraphs, http_error
from agency_sdk.test.kg_paging_sim import entry, serve, walk

ORG = 2
CODES = [f"kg-{i:03d}" for i in range(1, 13)]
SHUFFLED = CODES[5:] + CODES[:5][::-1]  # unsorted insertion order, so the sort is observable

PARITY_CASES = [
    pytest.param(SHUFFLED, {}, id="defaults"),
    pytest.param(SHUFFLED, {"page": 1, "size": 5}, id="explicit"),
    pytest.param(SHUFFLED, {"page": 2, "size": 5}, id="last-partial"),
    pytest.param(SHUFFLED, {"page": 9, "size": 5}, id="past-end"),
    pytest.param(SHUFFLED, {"size": 0}, id="size-zero"),
    pytest.param(SHUFFLED, {"page": 0, "size": 10**9}, id="huge-size"),
    pytest.param(SHUFFLED, {"page": -1}, id="negative-page"),
    pytest.param(SHUFFLED, {"size": -1}, id="negative-size"),
    pytest.param([], {}, id="empty-catalogue"),
]


def _fake_with(codes):
    graphs = FakeGraphs()
    for code in codes:
        graphs.add_graph(code, name=f"Sample graph {code}", enabled=True)
    return graphs


def _outcome(call):
    """Normalise a list() call to comparable data: the result, or the error's status and body."""
    try:
        result = call()
    except requests.HTTPError as exc:
        return ("error", exc.response.status_code, exc.response.json())
    return ("ok", result.page, [(e.code, e.name, e.enabled) for e in result.items])


def test_fake_signature_matches_real():
    """TS-17."""
    assert inspect.signature(FakeGraphs.list) == inspect.signature(AgencyGraphsClient.list)


def test_fake_hangs_off_control_plane():
    """TS-25 (fake side)."""
    assert isinstance(FakeControlPlane().graphs, FakeGraphs)


@pytest.mark.parametrize("codes, paging", PARITY_CASES)
def test_fake_matches_real_client(monkeypatch, fake_credentials, codes, paging):
    """TS-18, TS-32: one case table, identical items, page metadata and errors from the fake and the real client."""
    serve(monkeypatch, [entry(c) for c in codes])
    real = AgencyGraphsClient(token_supplier=fake_credentials, base_url="http://cp.test")
    fake = _fake_with(codes)

    assert _outcome(lambda: fake.list(ORG, **paging)) == _outcome(lambda: real.list(ORG, **paging))


def test_fake_walk_matches_real(monkeypatch, fake_credentials):
    """TS-18, TS-32: the documented walk yields the same codes from both, for full and empty catalogues."""
    for codes in (SHUFFLED, []):
        serve(monkeypatch, [entry(c) for c in codes])
        real = AgencyGraphsClient(token_supplier=fake_credentials, base_url="http://cp.test")
        assert walk(_fake_with(codes), ORG, size=5) == walk(real, ORG, size=5) == sorted(codes)


def test_fake_defaults_sort_and_calls():
    """TS-19: sorted by code, default page 0 / size 10, calls record page and size as given."""
    graphs = _fake_with(["kg-011", *CODES[:10]][::-1])

    result = graphs.list(ORG)

    assert [e.code for e in result.items] == sorted(["kg-011", *CODES[:10]])[:10]
    assert type(result.page) is Page
    assert (result.page.page, result.page.size, result.page.total) == (0, 10, 11)
    assert graphs.calls[-1] == ("list", {"organisation_id": ORG, "page": None, "size": None})

    graphs.list(ORG, page=1, size=3)
    assert graphs.calls[-1] == ("list", {"organisation_id": ORG, "page": 1, "size": 3})


@pytest.mark.parametrize("paging", [{"page": -1}, {"size": -1}, {"page": -2, "size": -2}])
def test_fake_negative_values_raise_500(paging):
    """TS-20: negative page/size raise an HTTPError with status 500, as comand answers."""
    graphs = _fake_with(CODES)

    with pytest.raises(requests.HTTPError) as info:
        graphs.list(ORG, **paging)
    assert info.value.response.status_code == 500
    assert info.value.response.json()["error"]["type"] == "SERVICE_ERROR"


def test_fake_error_injection():
    """TS-20: the injected error still wins over paging, and add_graph still works after it is cleared."""
    graphs = FakeGraphs()
    graphs.error = http_error(500)

    with pytest.raises(requests.HTTPError) as info:
        graphs.list(ORG, page=0, size=5)
    assert info.value.response.status_code == 500

    graphs.error = None
    assert graphs.list(ORG).items == []
    graphs.add_graph("medical-ontology", name="Medical Ontology", enabled=True)
    assert [(e.code, e.enabled) for e in graphs.list(ORG).items] == [("medical-ontology", True)]


def test_fake_result_round_trips_and_sessions_keep_their_page():
    """TS-34: the fake builds the shared domain.Page; FakeSessions still builds the datasets_dto Page it needs."""
    result = _fake_with(CODES).list(ORG, page=1, size=4)

    assert type(result.page) is Page
    assert KnowledgeGraphList.model_validate(result.model_dump()) == result

    search = FakeControlPlane().sessions.search(ORG, labels={"k": "v"})
    assert type(search.page) is datasets_dto.Page
