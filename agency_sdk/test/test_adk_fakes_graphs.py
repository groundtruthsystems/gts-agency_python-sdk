"""Parity tests: ``FakeGraphs`` must match the real graphs delegate's signature."""

import inspect

import pytest
import requests

from agency_sdk.delegates.graphs_client import AgencyGraphsClient
from agency_sdk.delegates.graphs_dto import KnowledgeGraphList
from agency_sdk.test.adk_fakes import FakeControlPlane, FakeGraphs, http_error


def test_fake_signature_matches_real():
    """TS-22."""
    assert inspect.signature(FakeGraphs.list) == inspect.signature(AgencyGraphsClient.list)


def test_fake_hangs_off_control_plane():
    assert isinstance(FakeControlPlane().graphs, FakeGraphs)


def test_fake_list():
    graphs = FakeGraphs()
    assert graphs.list(2).items == []

    graphs.add_graph("medical-ontology", name="Medical Ontology", enabled=True)
    result = graphs.list(2)

    assert isinstance(result, KnowledgeGraphList)
    assert [(e.code, e.enabled) for e in result.items] == [("medical-ontology", True)]
    assert graphs.calls[-1] == ("list", {"organisation_id": 2})


def test_fake_error_injection():
    """TS-24."""
    graphs = FakeGraphs()
    graphs.error = http_error(500)

    with pytest.raises(requests.HTTPError) as info:
        graphs.list(2)
    assert info.value.response.status_code == 500

    graphs.error = None
    assert graphs.list(2).items == []
