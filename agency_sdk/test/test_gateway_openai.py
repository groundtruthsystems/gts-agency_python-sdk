"""Tests for the openai-client factory on AgencyGatewayClient.

The gateway hands back STANDARD openai clients wired to the gateway: base_url on
the gateway host (``/v1``), the ``x-org`` routing header as a default header, and
an httpx auth hook that stamps a fresh rotating bearer on every request (the
construction-time ``api_key`` is a placeholder — verified live 2026-07-07 that
the per-request Authorization override wins). ``openai`` is a core dependency, so
these tests import it directly (a missing core dep fails the module import).
"""

import builtins

import httpx
import openai

from agency_sdk.delegates.gateway_client import AgencyGatewayClient

import pytest


@pytest.fixture
def client(fake_credentials):
    return AgencyGatewayClient(
        token_supplier=fake_credentials,
        gateway_base_url="http://gw.test:4000/",
        org_id="2",
    )


def _assert_rotating_auth(httpx_client) -> None:
    """The wired httpx client must stamp a fresh bearer per request via auth_flow."""
    auth = httpx_client.auth
    assert isinstance(auth, httpx.Auth)
    request = httpx.Request("POST", "http://gw.test:4000/v1/chat/completions")
    next(auth.auth_flow(request))
    assert request.headers["Authorization"] == "Bearer test-token"  # from FakeCredentials


def test_openai_client_is_wired_to_gateway(client):
    oai = client.openai_client(max_retries=0)

    assert isinstance(oai, openai.OpenAI)
    assert str(oai.base_url) == "http://gw.test:4000/v1/"  # gateway host, /v1 path
    assert oai.default_headers.get("x-org") == "2"
    assert oai.max_retries == 0  # caller kwargs pass through
    _assert_rotating_auth(oai._client)


def test_async_openai_client_is_wired_to_gateway(client):
    aoai = client.async_openai_client()

    assert isinstance(aoai, openai.AsyncOpenAI)
    assert str(aoai.base_url) == "http://gw.test:4000/v1/"
    assert aoai.default_headers.get("x-org") == "2"
    _assert_rotating_auth(aoai._client)


def test_openai_clients_are_independent_instances(client):
    assert client.openai_client() is not client.openai_client()  # no cache: caller owns lifecycle


def test_openai_helper_auth_does_not_import_observability(client, monkeypatch):
    # M1: the gateway's rotating-bearer auth comes from the neutral core module
    # agency_sdk.auth_hooks, NOT the optional observability submodule. Block that
    # submodule entirely and the helper must still build a working auth hook.
    real_import = builtins.__import__

    def block_observability(name, *args, **kwargs):
        if name.startswith("agency_sdk.observability"):
            raise ImportError(f"observability blocked: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", block_observability)

    oai = client.openai_client(max_retries=0)  # must not reach into observability
    _assert_rotating_auth(oai._client)
