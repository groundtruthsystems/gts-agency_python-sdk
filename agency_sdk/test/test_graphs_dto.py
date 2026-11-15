"""DTO tests for the graphs delegate.

Shapes mirror comand's ``ListKnowledgeGraphsResponse`` (knowledge_graph_service.rs). Synthetic data only.
"""

import pytest
from pydantic import ValidationError

from agency_sdk.delegates.graphs_dto import KnowledgeGraphEntry, KnowledgeGraphList

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
