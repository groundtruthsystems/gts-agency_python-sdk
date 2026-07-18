"""Offline protocol tests for AgencySessionClient.

Pins the exact wire contract of the session update command
(`POST /api/sessions/{id}/_command {command:"update", organisation, update:{…}}`)
and the delegate's two-method surface. The agent INHERITS a dispatched session
via `attach` and reports progress via `update`; it must NEVER self-register, so
`register` is deliberately absent.
"""

import pytest

from agency_sdk.delegates.session_client import AgencySessionClient
from agency_sdk.delegates.session_dto import SessionCommandResponse, SessionStatus


@pytest.fixture
def client(fake_credentials):
    return AgencySessionClient(token_supplier=fake_credentials, base_url="http://cp.test/")


class TestAttach:
    def test_attach_records_the_session_as_update_target(self, client, stub_requests):
        client.attach("sess-abc")

        assert client.session_id == "sess-abc"
        assert stub_requests.calls == []  # no HTTP — inherit, do not create

    def test_register_is_not_exposed(self, client):
        assert not hasattr(client, "register")
        assert not hasattr(client, "register_session")


class TestUpdate:
    def test_update_posts_command_envelope_for_attached_session(self, client, stub_requests):
        stub_requests.queue(json_data={"success": True, "message": "updated"})
        client.attach("sess-abc")

        result = client.update(organisation_id=2, status=SessionStatus.IN_PROGRESS)

        call = stub_requests.calls[0]
        assert call.method == "POST"
        assert call.url == "http://cp.test/api/sessions/sess-abc/_command"
        assert call.kwargs["params"] == {"o": "2"}
        assert call.kwargs["json"] == {
            "command": "update",
            "organisation": 2,
            "update": {"status": 2},
        }
        assert isinstance(result, SessionCommandResponse)
        assert result.success is True

    def test_update_marshals_plain_int_status_without_inferring(self, client, stub_requests):
        stub_requests.queue(json_data={"success": True, "message": "ok"})

        client.update(organisation_id=2, status=-1, session_id="sess-x")

        assert stub_requests.calls[0].kwargs["json"]["update"] == {"status": -1}

    def test_update_includes_only_provided_optional_fields(self, client, stub_requests):
        stub_requests.queue(json_data={"success": True, "message": "ok"})

        client.update(
            organisation_id=2,
            status=SessionStatus.COMPLETED,
            session_id="sess-x",
            result={"notes": 17},
            events=[{"event_type": "done"}],
            metrics={"tokens": 5},
            error="boom",
            logs="line1\nline2",
        )

        update = stub_requests.calls[0].kwargs["json"]["update"]
        assert update == {
            "status": 0,
            "result": {"notes": 17},
            "events": [{"event_type": "done"}],
            "metrics": {"tokens": 5},
            "error": "boom",
            "logs": "line1\nline2",
        }

    def test_update_explicit_session_id_overrides_attached(self, client, stub_requests):
        stub_requests.queue(json_data={"success": True, "message": "ok"})
        client.attach("attached")

        client.update(organisation_id=2, status=SessionStatus.IN_PROGRESS, session_id="explicit")

        assert stub_requests.calls[0].url == "http://cp.test/api/sessions/explicit/_command"

    def test_update_without_target_raises_before_network(self, client, stub_requests):
        with pytest.raises(ValueError):
            client.update(organisation_id=2, status=SessionStatus.IN_PROGRESS)

        assert stub_requests.calls == []

    def test_update_propagates_http_errors(self, client, stub_requests):
        import requests

        stub_requests.queue(json_data={"message": "not found"}, status_code=404)
        client.attach("missing")

        with pytest.raises(requests.HTTPError):
            client.update(organisation_id=2, status=SessionStatus.IN_PROGRESS)

    def test_update_retries_a_transient_connection_error(self, client, monkeypatch):
        # update is idempotent, so it opts into connection-retry — a transient blip on the
        # first send must not surface to the agent's reporting path.
        import requests

        from agency_sdk.test.conftest import StubResponse

        monkeypatch.setattr("agency_sdk.delegates.base_client.time.sleep", lambda s: None)
        calls = {"n": 0}

        def flaky(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise requests.ConnectionError("blip")
            return StubResponse(json_data={"success": True, "message": "ok"})

        monkeypatch.setattr(requests, "request", flaky)
        client.attach("sess-abc")

        result = client.update(organisation_id=2, status=SessionStatus.IN_PROGRESS)

        assert result.success is True
        assert calls["n"] == 2  # retried past the transient blip
