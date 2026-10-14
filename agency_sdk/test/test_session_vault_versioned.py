"""Offline protocol tests for the session-vault versioning, conditional-write, prefix and scope extensions.

These pin the Agent Vault backend contract (``docs/agent_vault_gap_analysis.md`` §4 items 1–4): a monotonic
per-key ``version``, ``if_version`` (412 on mismatch) / ``if_absent`` (409 if the key exists), a lexicographic
``prefix`` list with ``p``/``s`` paging, and ``scope=app|user`` resolved server-side from the session. The
existing calls must keep their exact wire shape (TS-44).
"""

import pytest
import requests

from agency_sdk.delegates.session_vault_client import AgencySessionVaultClient, ConditionalWriteFailed
from agency_sdk.delegates.session_vault_dto import VaultScope

SESSION_ID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def client(fake_credentials):
    return AgencySessionVaultClient(token_supplier=fake_credentials, base_url="http://cp.test/")


class TestVersion:
    def test_get_exposes_version(self, client, stub_requests):
        stub_requests.queue(json_data={"key": "adk.session", "value": {}, "classification": "restricted", "version": 7})

        entry = client.get(organisation_id=2, session_id=SESSION_ID, key="adk.session")

        assert entry.version == 7

    def test_set_exposes_version(self, client, stub_requests):
        stub_requests.queue(json_data={"key": "adk.session", "classification": "restricted", "version": 8})

        result = client.set(organisation_id=2, session_id=SESSION_ID, key="adk.session", value={})

        assert result.version == 8

    def test_version_is_optional_for_older_servers(self, client, stub_requests):
        stub_requests.queue(json_data={"key": "notes", "value": 1, "classification": "public"})

        assert client.get(organisation_id=2, session_id=SESSION_ID, key="notes").version is None

    def test_list_entries_expose_version(self, client, stub_requests):
        stub_requests.queue(
            json_data={
                "entries": [
                    {"key": "adk.ev.1", "size": 10, "updated_at": "t", "classification": "restricted", "version": 1}
                ]
            }
        )

        listing = client.list(organisation_id=2, session_id=SESSION_ID)

        assert listing.entries[0].version == 1


class TestConditionalWrites:
    def test_if_version_sends_param_and_opts_into_retry(self, client, stub_requests, monkeypatch):
        stub_requests.queue(json_data={"key": "adk.session", "classification": "restricted", "version": 4})

        client.set(organisation_id=2, session_id=SESSION_ID, key="adk.session", value={"a": 1}, if_version=3)

        call = stub_requests.calls[0]
        assert call.kwargs["params"] == {"o": "2", "if_version": "3"}
        assert call.kwargs["json"] == {"a": 1}

    def test_conditional_write_is_retried_on_connection_error(self, client, monkeypatch):
        attempts = []

        def flaky(method, url, **kwargs):
            attempts.append(1)
            if len(attempts) < 2:
                raise requests.ConnectionError("reset")
            from agency_sdk.test.conftest import StubResponse

            return StubResponse(json_data={"key": "k", "classification": "restricted", "version": 2})

        monkeypatch.setattr(requests, "request", flaky)
        monkeypatch.setattr("agency_sdk.delegates.base_client.time.sleep", lambda s: None)

        client.set(organisation_id=2, session_id=SESSION_ID, key="k", value=1, if_absent=True)

        assert len(attempts) == 2

    def test_unconditional_write_is_not_retried(self, client, monkeypatch):
        attempts = []

        def broken(method, url, **kwargs):
            attempts.append(1)
            raise requests.ConnectionError("reset")

        monkeypatch.setattr(requests, "request", broken)

        with pytest.raises(requests.ConnectionError):
            client.set(organisation_id=2, session_id=SESSION_ID, key="k", value=1)

        assert len(attempts) == 1

    def test_if_absent_sends_param(self, client, stub_requests):
        stub_requests.queue(json_data={"key": "adk.ev.1", "classification": "restricted", "version": 1})

        client.set(organisation_id=2, session_id=SESSION_ID, key="adk.ev.1", value={}, if_absent=True)

        assert stub_requests.calls[0].kwargs["params"] == {"o": "2", "if_absent": "true"}

    def test_412_raises_conditional_write_failed_version_mismatch(self, client, stub_requests):
        stub_requests.queue(json_data={"error": {"type": "PRECONDITION_FAILED"}}, status_code=412)

        with pytest.raises(ConditionalWriteFailed) as exc:
            client.set(organisation_id=2, session_id=SESSION_ID, key="adk.session", value={}, if_version=3)

        assert exc.value.reason == "version_mismatch"
        assert isinstance(exc.value, requests.HTTPError)

    def test_409_raises_conditional_write_failed_exists(self, client, stub_requests):
        stub_requests.queue(json_data={"error": {"type": "CONFLICT"}}, status_code=409)

        with pytest.raises(ConditionalWriteFailed) as exc:
            client.set(organisation_id=2, session_id=SESSION_ID, key="adk.ev.1", value={}, if_absent=True)

        assert exc.value.reason == "exists"

    def test_other_http_errors_propagate_unchanged(self, client, stub_requests):
        stub_requests.queue(json_data=None, status_code=500)

        with pytest.raises(requests.HTTPError) as exc:
            client.set(organisation_id=2, session_id=SESSION_ID, key="k", value={}, if_version=1)

        assert not isinstance(exc.value, ConditionalWriteFailed)

    def test_if_version_and_if_absent_are_mutually_exclusive(self, client, stub_requests):
        with pytest.raises(ValueError):
            client.set(organisation_id=2, session_id=SESSION_ID, key="k", value={}, if_version=1, if_absent=True)

        assert stub_requests.calls == []


class TestPrefixListing:
    def test_prefix_and_paging_params(self, client, stub_requests):
        stub_requests.queue(json_data={"entries": []})

        client.list(organisation_id=2, session_id=SESSION_ID, prefix="adk.ev.", page=1, size=100)

        assert stub_requests.calls[0].kwargs["params"] == {"o": "2", "prefix": "adk.ev.", "p": "1", "s": "100"}

    def test_invalid_prefix_raises_before_network(self, client, stub_requests):
        with pytest.raises(ValueError):
            client.list(organisation_id=2, session_id=SESSION_ID, prefix="../x")

        assert stub_requests.calls == []


class TestScope:
    @pytest.mark.parametrize("scope", [VaultScope.APP, VaultScope.USER, "user"])
    def test_scope_is_a_param_never_a_path_or_identity(self, client, stub_requests, scope):
        stub_requests.queue(json_data={"key": "adk.state", "value": {}, "classification": "restricted", "version": 1})
        stub_requests.queue(json_data={"key": "adk.state", "classification": "restricted", "version": 2})
        stub_requests.queue(json_data={"entries": []})

        client.get(organisation_id=2, session_id=SESSION_ID, key="adk.state", scope=scope)
        client.set(organisation_id=2, session_id=SESSION_ID, key="adk.state", value={}, scope=scope)
        client.list(organisation_id=2, session_id=SESSION_ID, scope=scope)

        for call in stub_requests.calls:
            assert call.kwargs["params"]["scope"] == str(scope)
            assert f"/api/sessions/{SESSION_ID}/vault" in call.url
            assert "user_id" not in call.kwargs["params"] and "app" not in call.kwargs["params"]

    def test_unknown_scope_raises_before_network(self, client, stub_requests):
        with pytest.raises(ValueError):
            client.get(organisation_id=2, session_id=SESSION_ID, key="adk.state", scope="tenant")

        assert stub_requests.calls == []

    def test_delete_accepts_scope(self, client, stub_requests):
        stub_requests.queue(json_data=None)

        client.delete(organisation_id=2, session_id=SESSION_ID, key="adk.state", scope=VaultScope.APP)

        assert stub_requests.calls[0].kwargs["params"] == {"o": "2", "scope": "app"}
