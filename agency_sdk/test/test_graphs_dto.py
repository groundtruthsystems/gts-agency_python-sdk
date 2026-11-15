"""DTO and exception tests for the graphs delegate.

Shapes mirror comand's ``ListKnowledgeGraphsResponse`` (knowledge_graph_service.rs) and the
medical ontology service's ``/api/models`` entity and ``_graph`` responses. Synthetic data only.
"""

import pytest
import requests
from pydantic import ValidationError

from agency_sdk.delegates.graphs_dto import (
    ConditionClassMismatch,
    ConditionNotFound,
    ConditionSubgraph,
    KnowledgeGraphEntry,
    KnowledgeGraphList,
    ResolvedEntity,
)

BID = "0f8fad5b-d9cb-469f-a165-70867728950e"
CANARY = "CANARY-TOKEN-NOT-REAL"


def test_knowledge_graph_list_parses_full_and_minimal_entries():
    body = {
        "items": [
            {
                "code": "medical-ontology",
                "name": "Medical Ontology",
                "description": "Built-in clinical knowledge graph",
                "enabled": True,
                "credential_variable_id": "var-1",
                "config": {"base_url": "https://example.invalid"},
            },
            {"code": "other", "name": "Other", "description": "", "enabled": False},
        ]
    }
    kg = KnowledgeGraphList.model_validate(body)

    assert [e.code for e in kg.items] == ["medical-ontology", "other"]
    assert kg.items[0].enabled is True
    assert kg.items[0].config == {"base_url": "https://example.invalid"}
    assert kg.items[1].credential_variable_id is None
    assert kg.items[1].config is None


@pytest.mark.parametrize("body", [{}, {"items": {}}, {"items": [{"name": "x", "description": "", "enabled": True}]}])
def test_knowledge_graph_list_rejects_malformed(body):
    with pytest.raises(ValidationError):
        KnowledgeGraphList.model_validate(body)


def test_repr_hides_config():
    """TS-20: config and credential_variable_id never appear in repr, but stay readable."""
    entry = KnowledgeGraphEntry(
        code="medical-ontology",
        name="Medical Ontology",
        description="",
        enabled=True,
        credential_variable_id=f"var-{CANARY}",
        config={"base_url": f"https://{CANARY}.example.invalid"},
    )

    assert CANARY not in repr(entry)
    assert CANARY not in str(entry)
    assert CANARY not in repr(KnowledgeGraphList(items=[entry]))
    assert entry.credential_variable_id == f"var-{CANARY}"
    assert entry.config == {"base_url": f"https://{CANARY}.example.invalid"}


def test_resolved_entity_accepts_uuid_bid_and_class():
    entity = ResolvedEntity.model_validate({"bid": BID, "class": "condition", "code": "C0000001", "name": "X"})

    assert entity.bid == BID
    assert entity.entity_class == "condition"


@pytest.mark.parametrize(
    "body",
    [
        {"class": "condition"},
        {"bid": BID},
        {"bid": "x/../y", "class": "condition"},
        {"bid": "B0000001", "class": "condition"},
        {"bid": BID + "\n", "class": "condition"},
        {"bid": 42, "class": "condition"},
        [],
        None,
    ],
)
def test_resolved_entity_rejects_malformed_or_unsafe(body):
    with pytest.raises(ValidationError):
        ResolvedEntity.model_validate(body)


def test_condition_subgraph_keeps_raw_documents():
    root = {"bid": BID, "class": "condition", "code": "C0000001"}
    sg = ConditionSubgraph(root=root, vertices=[root], edges=[{"label": "treats"}])

    assert sg.root == root
    assert sg.vertices == [root]
    assert sg.edges == [{"label": "treats"}]


def test_condition_subgraph_empty_lists_are_valid():
    sg = ConditionSubgraph(root={"bid": BID, "class": "condition"}, vertices=[], edges=[])
    assert sg.vertices == [] and sg.edges == []


@pytest.mark.parametrize("walk", [{"edges": []}, {"vertices": []}, {"vertices": [], "edges": {}}])
def test_condition_subgraph_rejects_malformed_walk(walk):
    with pytest.raises(ValidationError):
        ConditionSubgraph(root={"bid": BID, "class": "condition"}, **walk)


def test_condition_not_found_is_http_error_keeping_response():
    response = requests.Response()
    response.status_code = 404
    exc = ConditionNotFound(response=response)

    assert isinstance(exc, requests.HTTPError)
    assert exc.response is response
    assert "not found" in str(exc).lower()


def test_condition_class_mismatch_is_lookup_error_not_http_error():
    exc = ConditionClassMismatch("medication")

    assert isinstance(exc, LookupError)
    assert not isinstance(exc, requests.HTTPError)
    assert exc.entity_class == "medication"
