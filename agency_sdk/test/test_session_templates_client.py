"""Offline protocol tests for AgencySessionTemplatesClient.

The dispatcher resolves a template NAME → id via this list (client-side matching
+ caching + degraded handling live in the agent, not here).
"""

import pytest

from agency_sdk.delegates.session_templates_client import AgencySessionTemplatesClient
from agency_sdk.test.test_session_templates_dto import TEMPLATE_JSON


@pytest.fixture
def client(fake_credentials):
    return AgencySessionTemplatesClient(token_supplier=fake_credentials, base_url="http://cp.test/")


def test_list_hits_session_templates_endpoint_paged(client, stub_requests):
    stub_requests.queue(json_data={"page": {"page": 0, "size": 1, "total": 1}, "items": [TEMPLATE_JSON]})

    result = client.list(organisation_id=2)

    call = stub_requests.calls[0]
    assert call.method == "GET"
    assert call.url == "http://cp.test/api/session_templates"
    assert call.kwargs["params"] == {"o": "2", "p": "0", "s": "50"}
    assert [t.name for t in result.items] == ["Guideline Extraction (A)"]
    assert result.items[0].id == "98d227ab-4495-4730-b93a-5a7b8251f977"


def test_list_forwards_pagination(client, stub_requests):
    stub_requests.queue(json_data={"page": {"page": 1, "size": 5, "total": 0}, "items": []})

    client.list(organisation_id=9, page=1, size=5)

    assert stub_requests.calls[0].kwargs["params"] == {"o": "9", "p": "1", "s": "5"}
