"""Offline protocol tests for the Agent Vault additions to the session and session-template delegates.

Contract: ``docs/agent_vault_gap_analysis.md`` §4 item 5 (labels, correlation lookup, search) and item 8
(``AWAITING_INPUT``). The correlation id travels only in POST bodies, never a URL (TS-62), and a lookup
miss is indistinguishable from a foreign-org hit (TS-33). ``register`` lives on the templates client;
the session client still never registers (TS-47).
"""

import logging

import pytest
import requests

from agency_sdk.credentials import CredentialsSupplier
from agency_sdk.delegates.session_client import AgencySessionClient
from agency_sdk.delegates.session_dto import SessionRef, SessionStatus
from agency_sdk.delegates.session_templates_client import (
    AgencySessionTemplatesClient,
    CorrelationIdConflict,
    SessionTemplateNotFound,
)

CID = "corr-PHI-SENTINEL-0001"
PAGE = {"page": {"page": 0, "size": 50, "total_elements": 2, "total_pages": 1}}


def _templates(*names):
    return {
        **PAGE,
        "items": [{"id": f"tpl-{n}", "name": n, "organisation_id": 2} for n in names],
    }


@pytest.fixture
def sessions(fake_credentials):
    return AgencySessionClient(token_supplier=fake_credentials, base_url="http://cp.test/")


@pytest.fixture
def templates(fake_credentials):
    return AgencySessionTemplatesClient(token_supplier=fake_credentials, base_url="http://cp.test/")


class TestStatus:
    def test_awaiting_input_is_a_status(self):
        assert int(SessionStatus.AWAITING_INPUT) == 3

    def test_update_sends_awaiting_input_and_labels(self, sessions, stub_requests):
        stub_requests.queue(json_data={"success": True, "message": "ok"})

        sessions.update(2, status=SessionStatus.AWAITING_INPUT, labels={"adk_app": "a"}, session_id="s1")

        assert stub_requests.calls[0].kwargs["json"] == {
            "command": "update",
            "organisation": 2,
            "update": {"status": 3, "labels": {"adk_app": "a"}},
        }

    def test_session_client_still_never_registers(self, sessions):
        assert not hasattr(sessions, "register")
        assert callable(sessions.attach) and callable(sessions.update)
        assert callable(sessions.find_by_correlation_id)


class TestLookup:
    def test_lookup_posts_the_correlation_id_in_the_body(self, sessions, stub_requests):
        stub_requests.queue(json_data={"session_id": "s1", "status": 2, "labels": {"correlation_id": CID}})

        ref = sessions.find_by_correlation_id(2, CID)

        call = stub_requests.calls[0]
        assert call.method == "POST"
        assert call.url == "http://cp.test/api/sessions/_lookup"
        assert call.kwargs["params"] == {"o": "2"}
        assert call.kwargs["json"] == {"organisation": 2, "correlation_id": CID}
        assert CID not in call.url
        assert ref == SessionRef(session_id="s1", status=2, labels={"correlation_id": CID})

    def test_miss_and_foreign_hit_are_both_none(self, sessions, stub_requests, caplog):
        """TS-33: a miss and a session in another org both come back as 404 -> None, logged identically."""
        stub_requests.queue(json_data={"error": {"type": "NOT_FOUND"}}, status_code=404)
        stub_requests.queue(json_data={"error": {"type": "NOT_FOUND"}}, status_code=404)

        with caplog.at_level(logging.DEBUG):
            miss = sessions.find_by_correlation_id(2, "never-seen")
            first_logs = list(caplog.messages)
            caplog.clear()
            foreign = sessions.find_by_correlation_id(2, "other-org")

        assert miss is None and foreign is None
        assert first_logs == caplog.messages

    def test_lookup_error_text_never_carries_the_correlation_id(self, sessions, stub_requests):
        """TS-62: a forced error must not leak the id through exception text."""
        stub_requests.queue(json_data=None, status_code=500)

        with pytest.raises(requests.HTTPError) as exc:
            sessions.find_by_correlation_id(2, CID)

        assert CID not in str(exc.value)


class TestSearch:
    def test_search_posts_labels_and_optional_user(self, sessions, stub_requests):
        stub_requests.queue(
            json_data={**PAGE, "items": [{"session_id": "s1", "status": 2, "labels": {"adk_app": "a"}}]}
        )

        result = sessions.search(2, labels={"adk_app": "a"}, user="u-1", page=0, size=20)

        call = stub_requests.calls[0]
        assert call.method == "POST"
        assert call.url == "http://cp.test/api/sessions/_search"
        assert call.kwargs["json"] == {"organisation": 2, "labels": {"adk_app": "a"}, "user": "u-1"}
        assert call.kwargs["params"] == {"o": "2", "p": "0", "s": "20"}
        assert [r.session_id for r in result.items] == ["s1"]

    def test_search_without_user_omits_it(self, sessions, stub_requests):
        stub_requests.queue(json_data={**PAGE, "items": []})

        sessions.search(2, labels={"adk_app": "a"})

        assert "user" not in stub_requests.calls[0].kwargs["json"]


class TestRegister:
    def test_register_resolves_name_then_posts_labels_and_owner(self, templates, stub_requests):
        stub_requests.queue(json_data=_templates("other", "adk-agent"))
        stub_requests.queue(json_data={"success": True, "register": {"session_id": "s-new"}})

        session_id = templates.register(
            2, "adk-agent", correlation_id=CID, labels={"adk_app": "a"}, input={"q": 1}, owner="user-7"
        )

        assert session_id == "s-new"
        post = stub_requests.calls[1]
        assert post.method == "POST"
        assert post.url == "http://cp.test/api/session_templates/tpl-adk-agent/_command"
        assert post.kwargs["json"] == {
            "command": "register",
            "organisation": 2,
            "register": {
                "input": {"q": 1},
                "labels": {"adk_app": "a", "correlation_id": CID},
                "owner": "user-7",
            },
        }
        assert CID not in post.url

    def test_register_pages_through_templates(self, templates, stub_requests):
        many = {
            "page": {"page": 0, "size": 1, "total_elements": 2, "total_pages": 2},
            "items": [{"id": "tpl-x", "name": "x", "organisation_id": 2}],
        }
        stub_requests.queue(json_data=many)
        stub_requests.queue(
            json_data={**_templates("adk-agent"), "page": {"page": 1, "size": 1, "total_elements": 2, "total_pages": 2}}
        )
        stub_requests.queue(json_data={"success": True, "register": {"session_id": "s-new"}})

        assert templates.register(2, "adk-agent", correlation_id=CID) == "s-new"

    def test_unknown_template_raises_before_register(self, templates, stub_requests):
        stub_requests.queue(json_data=_templates("other"))

        with pytest.raises(SessionTemplateNotFound):
            templates.register(2, "adk-agent", correlation_id=CID)

        assert len(stub_requests.calls) == 1

    def test_duplicate_correlation_id_raises_with_existing_session(self, templates, stub_requests):
        stub_requests.queue(json_data=_templates("adk-agent"))
        stub_requests.queue(
            json_data={"error": {"type": "CONFLICT", "details": {"session_id": "s-existing"}}}, status_code=409
        )

        with pytest.raises(CorrelationIdConflict) as exc:
            templates.register(2, "adk-agent", correlation_id=CID)

        assert exc.value.existing_session_id == "s-existing"
        assert CID not in str(exc.value)


class TestNoSecretLeaks:
    def test_credentials_repr_and_fetch_logs_never_show_secrets(self, stub_requests, caplog):
        """TS-37: a fake secret/token never appears in repr or log output."""
        secret, token = "SECRET-SENTINEL-0002", "TOKEN-SENTINEL-0003"
        stub_requests.queue(json_data={"access_token": token})
        cs = CredentialsSupplier("http://auth", "id", secret)

        with caplog.at_level(logging.DEBUG):
            cs.bearer_token()

        rendered = repr(cs) + str(cs) + caplog.text
        assert secret not in rendered and token not in rendered
