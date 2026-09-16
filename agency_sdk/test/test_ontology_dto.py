"""DTO tests for the ontology export snapshot.

JSON transcribed from `OntologySnapshot` / `EntitySnapshot` / `RelationSnapshot`
/ `PropertySnapshot` in gts-agency `crates/ontology/src/api/dto.rs`. The export
handler pretty-prints that struct with serde's default snake_case field names
(no `rename_all`), so these models use raw snake_case — unlike the camelCase
mappings DTOs in the same module.
"""

from agency_sdk.delegates.ontology_dto import (
    BINARY_EXPORT_FORMATS,
    EntitySnapshot,
    Ontology,
    OntologySnapshot,
    OntologyStatus,
    PropertySnapshot,
    RelationSnapshot,
    is_binary_export_format,
)

#: List-view item transcribed from `Ontology` in dto.rs (snake_case, no rename_all).
ONTOLOGY_JSON = {
    "id": "b8a45108-9952-4202-88d6-5cb1fadea23d",
    "name": "claims",
    "display_name": "Claims",
    "description": "Working claims ontology",
    "status": "published",
    "kind": "domain",
    "created_at": "2026-08-01T00:00:00Z",
    "updated_at": "2026-08-31T00:00:00Z",
    "organization_id": 2,
}

#: Exact structure from `test_ontology_snapshot_from_raw_json` in dto.rs.
SNAPSHOT_JSON = {
    "entities": {
        "entity_1768310108685_hkrzizdts": {
            "id": "entity_1768310108685_hkrzizdts",
            "uri": "test",
            "label": "Test",
            "description": "A general test",
            "entity_type": "Class",
            "properties": {
                "id": {"type": "string", "required": True},
                "description": {"type": "string", "required": True},
            },
        },
        "entity_1768310149294_rzjag4906": {
            "id": "entity_1768310149294_rzjag4906",
            "uri": "unit-test",
            "label": "Unit Test",
            "description": "A test for a single unit of work.",
            "entity_type": "Class",
            "properties": {
                "id": {"type": "string", "required": True},
                "description": {"type": "string", "required": True},
            },
        },
    },
    "relations": {
        "relationship_1768310190610_vt4ivit9w": {
            "id": "relationship_1768310190610_vt4ivit9w",
            "uri": "isa",
            "label": "isa",
            "description": "",
            "source_id": "entity_1768310149294_rzjag4906",
            "target_id": "entity_1768310108685_hkrzizdts",
            "relation_type": "ObjectProperty",
            "properties": {},
        }
    },
}


def test_snapshot_deserialises_entities_and_relations():
    snapshot = OntologySnapshot.model_validate(SNAPSHOT_JSON)

    assert len(snapshot.entities) == 2
    entity = snapshot.entities["entity_1768310108685_hkrzizdts"]
    assert isinstance(entity, EntitySnapshot)
    assert entity.label == "Test"
    assert entity.entity_type == "Class"
    assert entity.properties["id"] == {"type": "string", "required": True}
    assert entity.aliases == []

    relation = snapshot.relations["relationship_1768310190610_vt4ivit9w"]
    assert isinstance(relation, RelationSnapshot)
    assert relation.source_id == "entity_1768310149294_rzjag4906"
    assert relation.target_id == "entity_1768310108685_hkrzizdts"
    assert relation.relation_type == "ObjectProperty"
    assert relation.property_id is None
    assert relation.min_count is None
    assert relation.max_count is None

    assert snapshot.properties == {}


def test_empty_snapshot_defaults_all_maps():
    snapshot = OntologySnapshot.model_validate({})

    assert snapshot.entities == {}
    assert snapshot.relations == {}
    assert snapshot.properties == {}


def test_property_snapshot_optional_sub_property_of():
    declared = PropertySnapshot.model_validate(
        {
            "id": "prop_has_role",
            "uri": "hasRole",
            "label": "has role",
            "description": "A role held by the subject",
            "sub_property_of": "prop_has_attribute",
        }
    )

    assert declared.sub_property_of == "prop_has_attribute"
    assert declared.properties == {}


def test_snapshot_accepts_additive_server_fields():
    snapshot = OntologySnapshot.model_validate({**SNAPSHOT_JSON, "future_field": "ok"})

    assert snapshot.entities["entity_1768310108685_hkrzizdts"].label == "Test"


def test_ontology_list_item_deserialises_snake_case_fields():
    ontology = Ontology.model_validate(ONTOLOGY_JSON)

    assert ontology.id == "b8a45108-9952-4202-88d6-5cb1fadea23d"
    assert ontology.name == "claims"
    assert ontology.display_name == "Claims"
    assert ontology.status == OntologyStatus.PUBLISHED
    assert ontology.kind == "domain"
    assert ontology.organization_id == 2


def test_ontology_kind_defaults_to_domain_when_omitted():
    payload = {k: v for k, v in ONTOLOGY_JSON.items() if k != "kind"}
    ontology = Ontology.model_validate(payload)

    assert ontology.kind == "domain"


def test_ontology_display_name_is_optional():
    payload = {k: v for k, v in ONTOLOGY_JSON.items() if k != "display_name"}
    ontology = Ontology.model_validate(payload)

    assert ontology.display_name is None


def test_binary_export_formats_match_server_aliases():
    assert BINARY_EXPORT_FORMATS == frozenset({"package-zip", "package-separate", "zip"})
    assert is_binary_export_format("package-zip")
    assert is_binary_export_format("ZIP")
    assert not is_binary_export_format("package")
    assert not is_binary_export_format(None)
    assert not is_binary_export_format("json")
