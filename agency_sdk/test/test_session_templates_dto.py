"""DTO tests for the session-templates delegate (name→id resolution vehicle).

JSON transcribed from the live server `GET /api/session_templates?o=` items.
"""

from agency_sdk.delegates.session_templates_dto import (
    SessionTemplateResponse,
    SessionTemplatesPagedResult,
)

TEMPLATE_JSON = {
    "id": "98d227ab-4495-4730-b93a-5a7b8251f977",
    "organisation_id": 2,
    "name": "Guideline Extraction (A)",
    "type": "A",
    "executed": 0,
    "audit_data": {"created_on": "2026-07-20 20:47:04Z", "created_by": "901"},
}


def test_session_template_response_deserialises():
    template = SessionTemplateResponse(**TEMPLATE_JSON)

    assert template.id == "98d227ab-4495-4730-b93a-5a7b8251f977"
    assert template.name == "Guideline Extraction (A)"
    assert template.organisation_id == 2
    assert template.type == "A"
    assert template.executed == 0
    assert template.audit_data == {"created_on": "2026-07-20 20:47:04Z", "created_by": "901"}


def test_session_template_optional_fields_default_none():
    template = SessionTemplateResponse(id="x", organisation_id=2, name="n")

    assert template.type is None
    assert template.executed is None
    assert template.audit_data is None


def test_session_templates_paged_result_wraps_page_and_items():
    result = SessionTemplatesPagedResult(**{"page": {"page": 0, "size": 1, "total": 1}, "items": [TEMPLATE_JSON]})

    assert result.page.total == 1
    assert [t.id for t in result.items] == ["98d227ab-4495-4730-b93a-5a7b8251f977"]
    assert result.items[0].name == "Guideline Extraction (A)"
