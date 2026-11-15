"""Parity tests: ``FakeGraphs`` must match the real graphs delegate's signatures and error contract."""

import inspect

import pytest
import requests

from agency_sdk.delegates.graphs_client import AgencyGraphsClient
from agency_sdk.delegates.graphs_dto import (
    ConditionClassMismatch,
    ConditionNotFound,
    ConditionSubgraph,
    KnowledgeGraphList,
)
from agency_sdk.test.adk_fakes import FakeControlPlane, FakeGraphs, http_error

BID = "0f8fad5b-d9cb-469f-a165-70867728950e"
ROOT = {"bid": BID, "class": "condition", "code": "C0000001"}


@pytest.mark.parametrize("name", ["list", "condition_subgraph"])
def test_fake_signatures_match_real(name):
    """TS-22."""
    assert inspect.signature(getattr(FakeGraphs, name)) == inspect.signature(getattr(AgencyGraphsClient, name))


def test_fake_hangs_off_control_plane():
    assert isinstance(FakeControlPlane().graphs, FakeGraphs)


def test_fake_list():
    graphs = FakeGraphs()
    assert graphs.list(2).items == []

    graphs.add_graph("medical-ontology", name="Medical Ontology", enabled=True)
    result = graphs.list(2)

    assert isinstance(result, KnowledgeGraphList)
    assert [(e.code, e.enabled) for e in result.items] == [("medical-ontology", True)]


def test_fake_condition_subgraph():
    """TS-23."""
    graphs = FakeGraphs()
    graphs.add_condition("C0000001", ROOT, vertices=[ROOT], edges=[{"label": "treats"}])
    graphs.add_condition("B0000002", {"bid": BID, "class": "medication"}, vertices=[], edges=[])

    found = graphs.condition_subgraph(2, "C0000001", depth=2, direction="out")
    assert isinstance(found, ConditionSubgraph)
    assert found.root == ROOT and found.edges == [{"label": "treats"}]
    assert graphs.calls[-1] == (
        "condition_subgraph",
        {"organisation_id": 2, "code": "C0000001", "depth": 2, "direction": "out"},
    )

    with pytest.raises(ConditionNotFound) as info:
        graphs.condition_subgraph(2, "C9999999")
    assert isinstance(info.value, requests.HTTPError)
    assert info.value.response.status_code == 404

    with pytest.raises(ConditionClassMismatch):
        graphs.condition_subgraph(2, "B0000002")

    for bad in ("c0000001", "C0000001\n", "C123"):
        with pytest.raises(ValueError):
            graphs.condition_subgraph(2, bad)
    with pytest.raises(ValueError):
        graphs.condition_subgraph(2, "C0000001", depth=0)
    with pytest.raises(ValueError):
        graphs.condition_subgraph(2, "C0000001", direction="up")


def test_fake_add_condition_rejects_bad_seed():
    with pytest.raises(ValueError):
        FakeGraphs().add_condition("X0000001", ROOT, vertices=[], edges=[])


def test_fake_error_injection():
    """TS-24."""
    graphs = FakeGraphs()
    graphs.add_condition("C0000001", ROOT, vertices=[], edges=[])
    graphs.error = http_error(500)

    with pytest.raises(requests.HTTPError) as info:
        graphs.list(2)
    assert info.value.response.status_code == 500
    with pytest.raises(requests.HTTPError) as info:
        graphs.condition_subgraph(2, "C0000001")
    assert not isinstance(info.value, ConditionNotFound)

    graphs.error = None
    assert graphs.condition_subgraph(2, "C0000001").root == ROOT
