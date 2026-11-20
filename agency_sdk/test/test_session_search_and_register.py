"""Offline protocol tests for session search, template register and ``resolve_session``.

Contract: comand ``documentation/AGENT_VAULT_API.md`` (track ``session_correlation_20261119``). A correlation
id names a task shared by many sessions; it travels only in POST bodies, never a URL (TS-62). ``register``
and ``resolve_session`` live on the templates client; the session client still never registers (TS-47), and
the removed ``POST /api/sessions/_lookup`` never existed on the server.
"""

import logging

import pytest
import requests

from agency_sdk.credentials import CredentialsSupplier
from agency_sdk.delegates import session_templates_client as templates_module
from agency_sdk.delegates.session_client import AgencySessionClient
from agency_sdk.delegates.session_dto import SessionStatus, SessionSummary
from agency_sdk.delegates.session_templates_client import (
    AgencySessionTemplatesClient,
    CorrelationMismatch,
    SessionAlreadyExists,
    SessionTemplateNotFound,
)
from agency_sdk.delegates.session_templates_dto import RegisteredSession, ResolvedSession

CID = "corr-PHI-SENTINEL-0001"
PAGE = {"page": {"total": 2, "page": 0, "size": 50}}
PHI = "PHI-SENTINEL-INPUT-0004"
OWNER = "U:OWNER-SENTINEL-0005"

#: A ``SessionResponse`` as comand's ``_search`` serialises it.
SESSION_RESPONSE = {
    "id": "s1",
    "template_id": "tpl-adk-agent",
    "template_name": "adk-agent",
    "name": "adk-agent",
    "type": "agent",
    "status": 2,
    "organisation": 2,
    "version": 1,
    "created_on": "2026-11-19T10:00:00Z",
    "storage_type": "object_storage",
    "input": {"note": PHI},
    "correlation_id": CID,
    "user_id": OWNER,
    "vault_version": 3,
}


def _templates(*names):
    return {
        **PAGE,
        "items": [{"id": f"tpl-{n}", "name": n, "organisation_id": 2} for n in names],
    }


def _conflict(code):
    return {"error": {"type": "CONFLICT", "message": "conflict", "details": {"code": code}}}


@pytest.fixture
def sessions(fake_credentials):
    return AgencySessionClient(token_supplier=fake_credentials, base_url="http://cp.test/")


@pytest.fixture
def templates(fake_credentials):
    return AgencySessionTemplatesClient(token_supplier=fake_credentials, base_url="http://cp.test/")


@pytest.fixture
def no_sleep(monkeypatch):
    waits = []
    monkeypatch.setattr(templates_module.time, "sleep", waits.append)
    return waits


class TestStatus:
    def test_awaiting_input_is_a_status(self):
        assert int(SessionStatus.AWAITING_INPUT) == 3

    def test_update_sends_awaiting_input_without_labels(self, sessions, stub_requests):
        stub_requests.queue(json_data={"success": True, "message": "ok"})

        sessions.update(2, status=SessionStatus.AWAITING_INPUT, session_id="s1")

        assert stub_requests.calls[0].kwargs["json"] == {
            "command": "update",
            "organisation": 2,
            "update": {"status": 3},
        }

    def test_session_client_never_registers_and_has_no_lookup(self, sessions):
        assert not hasattr(sessions, "register")
        assert not hasattr(sessions, "find_by_correlation_id")
        assert callable(sessions.attach) and callable(sessions.update) and callable(sessions.search)


class TestGet:
    def test_get_reads_one_session_without_an_org_param(self, sessions, stub_requests):
        """comand resolves the organisation from the record, so no ``o`` is sent."""
        stub_requests.queue(json_data=SESSION_RESPONSE)

        session = sessions.get("s1")

        call = stub_requests.calls[0]
        assert call.method == "GET"
        assert call.url == "http://cp.test/api/sessions/s1"
        assert not call.kwargs.get("params")
        assert (session.id, session.correlation_id, session.user_id) == ("s1", CID, OWNER)

    def test_get_404_propagates(self, sessions, stub_requests):
        stub_requests.queue(json_data={"error": {"type": "NOT_FOUND"}}, status_code=404)

        with pytest.raises(requests.HTTPError):
            sessions.get("s-gone")


class TestSearch:
    def test_search_posts_every_filter_in_the_body(self, sessions, stub_requests):
        stub_requests.queue(json_data={**PAGE, "items": [SESSION_RESPONSE]})

        result = sessions.search(
            2,
            correlation_id=CID,
            user_id=OWNER,
            template_id="tpl-adk-agent",
            status=[SessionStatus.IN_PROGRESS, 3],
            page=1,
            size=20,
            sort=("created_on", "asc"),
        )

        call = stub_requests.calls[0]
        assert call.method == "POST"
        assert call.url == "http://cp.test/api/sessions/_search"
        assert call.kwargs["params"] == {"o": "2"}
        assert call.kwargs["json"] == {
            "organisation": [2],
            "page": {"page": 1, "size": 20},
            "correlation_id": CID,
            "user_id": OWNER,
            "template_id": "tpl-adk-agent",
            "status": [2, 3],
            "sort": {"field": "created_on", "direction": "asc"},
        }
        assert CID not in call.url
        [item] = result.items
        assert (item.id, item.correlation_id, item.user_id, item.vault_version) == ("s1", CID, OWNER, 3)

    def test_search_without_filters_sends_only_org_and_page(self, sessions, stub_requests):
        stub_requests.queue(json_data={**PAGE, "items": []})

        sessions.search(2)

        assert stub_requests.calls[0].kwargs["json"] == {"organisation": [2], "page": {"page": 0, "size": 50}}

    def test_summary_repr_hides_phi_and_the_owner(self):
        rendered = repr(SessionSummary(**{**SESSION_RESPONSE, "result": {"x": PHI}, "error": PHI}))

        assert PHI not in rendered and OWNER not in rendered
        assert "s1" in rendered

    def test_correlation_id_and_user_id_are_required(self):
        with pytest.raises(ValueError):
            SessionSummary(**{k: v for k, v in SESSION_RESPONSE.items() if k != "correlation_id"})

    def test_search_error_text_never_carries_the_correlation_id(self, sessions, stub_requests):
        """TS-62: a forced error must not leak the id through exception text."""
        stub_requests.queue(json_data=None, status_code=500)

        with pytest.raises(requests.HTTPError) as exc:
            sessions.search(2, correlation_id=CID)

        assert CID not in str(exc.value)


class TestRegister:
    def test_register_sends_the_correlation_as_input_and_no_owner(self, templates, stub_requests):
        stub_requests.queue(json_data=_templates("other", "adk-agent"))
        stub_requests.queue(
            json_data={"success": True, "register": {"session_id": "s-new", "correlation_id": CID, "user_id": OWNER}}
        )

        registered = templates.register(2, "adk-agent", correlation_id=CID, input={"q": 1})

        assert registered == RegisteredSession(session_id="s-new", correlation_id=CID, user_id=OWNER)
        assert OWNER not in repr(registered)
        post = stub_requests.calls[1]
        assert post.url == "http://cp.test/api/session_templates/tpl-adk-agent/_command"
        assert post.kwargs["json"] == {
            "command": "register",
            "organisation": 2,
            "register": {"input": {"q": 1, "correlation": CID}},
        }
        assert CID not in post.url

    def test_register_without_a_correlation_leaves_the_input_alone(self, templates, stub_requests):
        stub_requests.queue(json_data=_templates("adk-agent"))
        stub_requests.queue(
            json_data={
                "success": True,
                "register": {"session_id": "s-new", "correlation_id": "s-new", "user_id": OWNER},
            }
        )

        registered = templates.register(2, "adk-agent")

        assert stub_requests.calls[1].kwargs["json"]["register"] == {"input": {}}
        assert registered.correlation_id == "s-new"

    def test_register_does_not_mutate_the_callers_input(self, templates, stub_requests):
        stub_requests.queue(json_data=_templates("adk-agent"))
        stub_requests.queue(
            json_data={"success": True, "register": {"session_id": "s", "correlation_id": CID, "user_id": OWNER}}
        )
        given = {"q": 1}

        templates.register(2, "adk-agent", correlation_id=CID, input=given)

        assert given == {"q": 1}

    def test_register_pages_through_templates(self, templates, stub_requests):
        many = {
            "page": {"total": 2, "page": 0, "size": 1},
            "items": [{"id": "tpl-x", "name": "x", "organisation_id": 2}],
        }
        stub_requests.queue(json_data=many)
        stub_requests.queue(json_data={**_templates("adk-agent"), "page": {"total": 2, "page": 1, "size": 1}})
        stub_requests.queue(
            json_data={"success": True, "register": {"session_id": "s-new", "correlation_id": CID, "user_id": OWNER}}
        )

        assert templates.register(2, "adk-agent", correlation_id=CID).session_id == "s-new"

    def test_unknown_template_raises_before_register(self, templates, stub_requests):
        stub_requests.queue(json_data=_templates("other"))

        with pytest.raises(SessionTemplateNotFound):
            templates.register(2, "adk-agent", correlation_id=CID)

        assert len(stub_requests.calls) == 1

    def test_a_409_is_no_longer_special(self, templates, stub_requests):
        """Many sessions may share a correlation id; any 409 is just an ``HTTPError``."""
        stub_requests.queue(json_data=_templates("adk-agent"))
        stub_requests.queue(json_data=_conflict("whatever"), status_code=409)

        with pytest.raises(requests.HTTPError) as exc:
            templates.register(2, "adk-agent", correlation_id=CID)

        assert type(exc.value) is requests.HTTPError


class TestResolveSession:
    def _resolved(self, created=False, status=2):
        return {
            "success": True,
            "message": "ok",
            "resolve_session": {"session_id": "s1", "created": created, "status": status, "vault_version": 4},
        }

    def test_find_or_create_posts_the_command_in_the_body(self, templates, stub_requests):
        stub_requests.queue(json_data=_templates("adk-agent"))
        stub_requests.queue(json_data=self._resolved(created=True, status=1), status_code=201)

        resolved = templates.resolve_session(
            2,
            "adk-agent",
            mode="find_or_create",
            correlation_id=CID,
            session_id="s-dispatched",
            input={"q": 1},
            state={"step": 0},
            framework_user_id="fw-user-1",
        )

        assert resolved == ResolvedSession(session_id="s1", created=True, status=1, vault_version=4)
        post = stub_requests.calls[1]
        assert post.method == "POST"
        assert post.url == "http://cp.test/api/session_templates/tpl-adk-agent/_command"
        assert post.kwargs["params"] == {"o": "2"}
        assert post.kwargs["json"] == {
            "command": "resolve_session",
            "organisation": 2,
            "resolve_session": {
                "mode": "find_or_create",
                "correlation_id": CID,
                "session_id": "s-dispatched",
                "input": {"q": 1},
                "state": {"step": 0},
                "user_id": "fw-user-1",
            },
        }
        assert CID not in post.url

    def test_find_sends_only_mode_and_correlation(self, templates, stub_requests):
        stub_requests.queue(json_data=_templates("adk-agent"))
        stub_requests.queue(json_data=self._resolved())

        assert templates.resolve_session(2, "adk-agent", mode="find", correlation_id=CID).session_id == "s1"
        assert stub_requests.calls[1].kwargs["json"]["resolve_session"] == {"mode": "find", "correlation_id": CID}

    def test_find_miss_and_unauthorised_caller_are_both_none(self, templates, stub_requests, caplog):
        """TS-33: comand answers a miss and a non-agent caller with the same 404."""
        for _ in range(2):
            stub_requests.queue(json_data=_templates("adk-agent"))
            stub_requests.queue(json_data={"error": {"type": "NOT_FOUND"}}, status_code=404)

        with caplog.at_level(logging.DEBUG):
            miss = templates.resolve_session(2, "adk-agent", mode="find", correlation_id="never-seen")
            first_logs = list(caplog.messages)
            caplog.clear()
            foreign = templates.resolve_session(2, "adk-agent", mode="find", correlation_id="not-mine-0001")

        assert miss is None and foreign is None
        assert first_logs == caplog.messages

    def test_a_404_outside_find_raises(self, templates, stub_requests):
        stub_requests.queue(json_data=_templates("adk-agent"))
        stub_requests.queue(json_data={"error": {"type": "NOT_FOUND"}}, status_code=404)

        with pytest.raises(requests.HTTPError):
            templates.resolve_session(2, "adk-agent", mode="find_or_create", correlation_id=CID)

    def test_correlation_mismatch_is_typed_and_names_no_id(self, templates, stub_requests):
        stub_requests.queue(json_data=_templates("adk-agent"))
        stub_requests.queue(json_data=_conflict("correlation_mismatch"), status_code=409)

        with pytest.raises(CorrelationMismatch) as exc:
            templates.resolve_session(
                2, "adk-agent", mode="find_or_create", correlation_id=CID, session_id="s-dispatched"
            )

        assert exc.value.response.status_code == 409
        assert CID not in str(exc.value) and "s-dispatched" not in str(exc.value)

    def test_create_of_an_existing_session_is_typed(self, templates, stub_requests):
        stub_requests.queue(json_data=_templates("adk-agent"))
        stub_requests.queue(json_data=_conflict("already_exists"), status_code=409)

        with pytest.raises(SessionAlreadyExists):
            templates.resolve_session(2, "adk-agent", mode="create", correlation_id=CID)

    def test_other_409s_propagate_untyped(self, templates, stub_requests):
        stub_requests.queue(json_data=_templates("adk-agent"))
        stub_requests.queue(json_data=_conflict("session_terminal"), status_code=409)

        with pytest.raises(requests.HTTPError) as exc:
            templates.resolve_session(2, "adk-agent", mode="find_or_create", correlation_id=CID, session_id="s")

        assert type(exc.value) is requests.HTTPError

    def test_a_contended_lock_is_retried_after_retry_after(self, templates, stub_requests, no_sleep):
        stub_requests.queue(json_data=_templates("adk-agent"))
        stub_requests.queue(json_data={"error": {"type": "SERVICE_ERROR"}}, status_code=503).headers[
            "Retry-After"
        ] = "1"
        stub_requests.queue(json_data=self._resolved(created=True), status_code=201)

        resolved = templates.resolve_session(2, "adk-agent", mode="find_or_create", correlation_id=CID)

        assert resolved is not None and resolved.created
        assert no_sleep == [1.0]
        assert len(stub_requests.calls) == 3

    def test_retry_after_is_capped(self, templates, stub_requests, no_sleep):
        stub_requests.queue(json_data=_templates("adk-agent"))
        stub_requests.queue(json_data=None, status_code=503).headers["Retry-After"] = "3600"
        stub_requests.queue(json_data=self._resolved())

        templates.resolve_session(2, "adk-agent", mode="find", correlation_id=CID)

        assert no_sleep == [5.0]

    def test_503_gives_up_after_three_attempts(self, templates, stub_requests, no_sleep):
        stub_requests.queue(json_data=_templates("adk-agent"))
        for _ in range(3):
            stub_requests.queue(json_data=None, status_code=503)

        with pytest.raises(requests.HTTPError) as exc:
            templates.resolve_session(2, "adk-agent", mode="find_or_create", correlation_id=CID)

        assert exc.value.response.status_code == 503
        assert no_sleep == [1.0, 1.0]


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
