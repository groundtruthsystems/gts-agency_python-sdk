"""DTO tests for the graphs delegate.

Shapes mirror comand's ``PagedResult<KnowledgeGraphEntry>`` (knowledge_graph_service.rs). Synthetic data only.
"""

import pytest
from pydantic import ValidationError

from agency_sdk.delegates import datasets_dto
from agency_sdk.delegates.graphs_dto import KnowledgeGraphEntry, KnowledgeGraphList
from agency_sdk.domain import Page

CANARY = "CANARY-TOKEN-NOT-REAL"


PAGE = {"page": 0, "size": 10, "total": 2}


def test_knowledge_graph_list_parses_full_and_minimal_entries():
    """TS-15: the paged body parses; ``page`` is the shared ``agency_sdk.domain.Page``."""
    body = {
        "page": PAGE,
        "extra_field": "kept",
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

    assert type(kg.page) is Page
    assert type(kg.page) is not datasets_dto.Page
    assert (kg.page.page, kg.page.size, kg.page.total) == (0, 10, 2)
    assert kg.model_extra == {"extra_field": "kept"}
    assert [e.code for e in kg.items] == ["medical-ontology", "other"]
    assert kg.items[0].enabled is True
    assert kg.items[0].config == {"base_url": "https://example.invalid"}
    assert kg.items[1].credential_variable_id is None
    assert kg.items[1].config is None


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"items": []},
        {"page": PAGE, "items": {}},
        {"page": PAGE, "items": [{"name": "x", "description": "", "enabled": True}]},
        {"page": {"page": 0, "size": 10, "total": "many"}, "items": []},
        {"page": {"page": 0, "total": 0}, "items": []},
        {"page": None, "items": []},
    ],
)
def test_knowledge_graph_list_rejects_malformed(body):
    """TS-13, TS-14: a body without a valid ``page`` (the old unpaged shape included) fails validation."""
    with pytest.raises(ValidationError):
        KnowledgeGraphList.model_validate(body)


def test_repr_hides_config():
    """TS-16: config and credential_variable_id never appear in repr, but stay readable; enabled parses."""
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
    paged = KnowledgeGraphList(page=Page(page=0, size=10, total=1), items=[entry])
    assert CANARY not in repr(paged)
    assert CANARY not in str(paged)
    assert entry.enabled is True
    assert entry.credential_variable_id == f"var-{CANARY}"
    assert entry.config == {"base_url": f"https://{CANARY}.example.invalid"}
