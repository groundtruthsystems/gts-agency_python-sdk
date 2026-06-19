"""Offline protocol tests for AgencySessionVaultClient.

Every test asserts the exact wire contract (URL, query params, headers, body)
verified against gts-agency-control/src/handler/sessions.rs and
src/service/sessions/session_vault_service.rs.
"""

import pytest

from agency_sdk.delegates.session_vault_client import AgencySessionVaultClient
from agency_sdk.delegates.session_vault_dto import Classification

SESSION_ID = "11111111-2222-3333-4444-555555555555"

LIST_JSON = {
    "entries": [
        {"key": "checkpoint", "size": 128, "updated_at": "2026-06-17T12:00:00Z", "classification": "restricted"},
        {"key": "notes", "size": 42, "updated_at": "2026-06-17T12:01:00Z", "classification": "public"},
    ]
}

ENTRY_JSON = {
    "key": "notes",
    "value": {"items": ["a", "b", "c"]},
    "classification": "public",
}


@pytest.fixture
def client(fake_credentials):
    return AgencySessionVaultClient(token_supplier=fake_credentials, base_url="http://cp.test/")


class TestList:
    def test_list_hits_vault_endpoint(self, client, stub_requests):
        stub_requests.queue(json_data=LIST_JSON)

        result = client.list(organisation_id=2, session_id=SESSION_ID)

        call = stub_requests.calls[0]
        assert call.method == "GET"
        assert call.url == f"http://cp.test/api/sessions/{SESSION_ID}/vault"
        assert call.kwargs["params"] == {"o": "2"}
        assert call.kwargs["headers"]["Authorization"] == "Bearer test-token"
        assert [e.key for e in result.entries] == ["checkpoint", "notes"]
        assert result.entries[0].size == 128


class TestGet:
    def test_get_without_reveal_omits_param(self, client, stub_requests):
        stub_requests.queue(json_data=ENTRY_JSON)

        result = client.get(organisation_id=2, session_id=SESSION_ID, key="notes")

        call = stub_requests.calls[0]
        assert call.method == "GET"
        assert call.url == f"http://cp.test/api/sessions/{SESSION_ID}/vault/notes"
        assert call.kwargs["params"] == {"o": "2"}
        assert result.value == {"items": ["a", "b", "c"]}
        assert result.classification == "public"

    def test_get_with_reveal_adds_param(self, client, stub_requests):
        stub_requests.queue(json_data={**ENTRY_JSON, "key": "draft", "classification": "confidential"})

        client.get(organisation_id=2, session_id=SESSION_ID, key="draft", reveal=True)

        call = stub_requests.calls[0]
        assert call.url == f"http://cp.test/api/sessions/{SESSION_ID}/vault/draft"
        assert call.kwargs["params"] == {"o": "2", "reveal": "true"}

    def test_get_invalid_key_raises_before_network(self, client, stub_requests):
        with pytest.raises(ValueError):
            client.get(organisation_id=2, session_id=SESSION_ID, key="bad/key")

        assert stub_requests.calls == []


class TestSet:
    def test_set_default_classification_omits_param_and_sends_raw_value(self, client, stub_requests):
        stub_requests.queue(json_data={"key": "checkpoint", "classification": "restricted"})
        value = {"step": 3, "stage": "awaiting_human_review"}

        result = client.set(organisation_id=2, session_id=SESSION_ID, key="checkpoint", value=value)

        call = stub_requests.calls[0]
        assert call.method == "PUT"
        assert call.url == f"http://cp.test/api/sessions/{SESSION_ID}/vault/checkpoint"
        assert call.kwargs["params"] == {"o": "2"}
        # The body is the raw JSON value, not an envelope.
        assert call.kwargs["json"] == value
        assert result.key == "checkpoint"
        assert result.classification == "restricted"

    def test_set_explicit_classification_adds_param(self, client, stub_requests):
        stub_requests.queue(json_data={"key": "notes", "classification": "public"})

        client.set(
            organisation_id=2,
            session_id=SESSION_ID,
            key="notes",
            value=["a", "b", "c"],
            classification=Classification.PUBLIC,
        )

        call = stub_requests.calls[0]
        assert call.kwargs["params"] == {"o": "2", "classification": "public"}
        # A list value is sent verbatim as the body.
        assert call.kwargs["json"] == ["a", "b", "c"]

    def test_set_invalid_key_raises_before_network(self, client, stub_requests):
        with pytest.raises(ValueError):
            client.set(organisation_id=2, session_id=SESSION_ID, key="", value={})

        assert stub_requests.calls == []


class TestDelete:
    def test_delete_issues_request_and_returns_none(self, client, stub_requests):
        stub_requests.queue(json_data={"success": True})

        result = client.delete(organisation_id=2, session_id=SESSION_ID, key="notes")

        call = stub_requests.calls[0]
        assert call.method == "DELETE"
        assert call.url == f"http://cp.test/api/sessions/{SESSION_ID}/vault/notes"
        assert call.kwargs["params"] == {"o": "2"}
        assert result is None

    def test_delete_invalid_key_raises_before_network(self, client, stub_requests):
        with pytest.raises(ValueError):
            client.delete(organisation_id=2, session_id=SESSION_ID, key="..")

        assert stub_requests.calls == []
