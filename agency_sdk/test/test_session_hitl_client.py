"""Offline protocol tests for ``AgencySessionHitlClient`` (contract §4 item 6).

The agent creates and reads human-input requests; a *human* principal answers them. The client used for
``respond`` must be built with the human's own token supplier — comand rejects service principals (403),
keeps the first answer (409 thereafter) and refuses an expired request (410).
"""

import pytest
import requests

from agency_sdk.delegates.session_hitl_client import (
    AgencySessionHitlClient,
    HitlAlreadyAnswered,
    HitlNotAuthorised,
    HitlRequestExpired,
)
from agency_sdk.delegates.session_hitl_dto import HitlRequest

SID = "sess-1"
REQ = {
    "request_id": "adk-call-1",
    "invocation_id": "e-inv-1",
    "status": "pending",
    "prompt": {"tool": "ask_human", "args": {"question": "PHI-SENTINEL-0001?"}},
    "expires_at": "2026-10-19T00:00:00Z",
    "created_at": "2026-10-12T00:00:00Z",
}


@pytest.fixture
def client(fake_credentials):
    return AgencySessionHitlClient(token_supplier=fake_credentials, base_url="http://cp.test/")


def test_create_posts_the_request(client, stub_requests):
    stub_requests.queue(json_data=REQ)

    created = client.create(
        2,
        SID,
        request_id="adk-call-1",
        invocation_id="e-inv-1",
        prompt=REQ["prompt"],
        expires_at="2026-10-19T00:00:00Z",
    )

    call = stub_requests.calls[0]
    assert (call.method, call.url) == ("POST", f"http://cp.test/api/sessions/{SID}/hitl")
    assert call.kwargs["params"] == {"o": "2"}
    assert call.kwargs["json"] == {
        "organisation": 2,
        "request_id": "adk-call-1",
        "invocation_id": "e-inv-1",
        "prompt": REQ["prompt"],
        "expires_at": "2026-10-19T00:00:00Z",
    }
    assert isinstance(created, HitlRequest) and created.status == "pending"


def test_create_is_retried_because_it_is_idempotent_on_request_id(client, monkeypatch):
    attempts = []

    def flaky(method, url, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise requests.ConnectionError("reset")
        from agency_sdk.test.conftest import StubResponse

        return StubResponse(json_data=REQ)

    monkeypatch.setattr(requests, "request", flaky)
    monkeypatch.setattr("agency_sdk.delegates.base_client.time.sleep", lambda s: None)

    client.create(2, SID, request_id="r", invocation_id="i", prompt={}, expires_at="t")

    assert len(attempts) == 2


def test_get_and_list(client, stub_requests):
    stub_requests.queue(json_data={**REQ, "status": "answered", "response": {"ok": True}, "responder": "u-9"})
    stub_requests.queue(json_data={"items": [REQ]})

    one = client.get(2, SID, "adk-call-1")
    many = client.list(2, SID, status="pending")

    assert stub_requests.calls[0].url == f"http://cp.test/api/sessions/{SID}/hitl/adk-call-1"
    assert stub_requests.calls[1].kwargs["params"] == {"o": "2", "status": "pending"}
    assert (one.responder, one.response) == ("u-9", {"ok": True})
    assert [r.request_id for r in many] == ["adk-call-1"]


def test_request_repr_hides_prompt_and_response(client):
    request = HitlRequest(**{**REQ, "response": {"note": "PHI-SENTINEL-0002"}, "responder": "u-9"})

    rendered = repr(request) + str(request)

    assert "PHI-SENTINEL-0001" not in rendered and "PHI-SENTINEL-0002" not in rendered


def test_respond_posts_the_answer(client, stub_requests):
    stub_requests.queue(json_data={**REQ, "status": "answered", "response": "yes", "responder": "u-9"})

    answered = client.respond(2, SID, "adk-call-1", "yes")

    call = stub_requests.calls[0]
    assert call.url == f"http://cp.test/api/sessions/{SID}/hitl/adk-call-1/_respond"
    assert call.kwargs["json"] == {"organisation": 2, "response": "yes"}
    assert answered.status == "answered"


@pytest.mark.parametrize(
    "status, error", [(409, HitlAlreadyAnswered), (410, HitlRequestExpired), (403, HitlNotAuthorised)]
)
def test_respond_maps_policy_outcomes_to_typed_errors(client, stub_requests, status, error):
    stub_requests.queue(json_data={"error": {"type": "X"}}, status_code=status)

    with pytest.raises(error) as exc:
        client.respond(2, SID, "adk-call-1", "yes")

    assert isinstance(exc.value, requests.HTTPError)


def test_facade_exposes_the_hitl_delegate(fake_credentials):
    from agency_sdk.client import AgencyClient

    client = AgencyClient(token_supplier=fake_credentials, base_url="http://cp.test")

    assert isinstance(client.session_hitl(), AgencySessionHitlClient)
