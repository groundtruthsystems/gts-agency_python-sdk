"""Tests for the [openai] extra helpers on AgencyGatewayClient.

The helpers return STANDARD openai clients wired to the gateway: base_url on
the gateway host, the ``x-org`` routing header as a default header, and an
httpx auth hook that stamps a fresh rotating bearer on every request (the
construction-time ``api_key`` is a placeholder — verified live 2026-07-07 that
a per-request Authorization override wins).

The guard test runs everywhere; the functional tests skip cleanly when the
``[openai]`` extra is not installed (observability packaging precedent).
"""

import builtins

import pytest

from agency_sdk.delegates.gateway_client import AgencyGatewayClient


@pytest.fixture
def client(fake_credentials):
    return AgencyGatewayClient(
        token_supplier=fake_credentials,
        gateway_base_url="http://gw.test:4000/",
        org_id="2",
    )


def test_missing_extra_raises_actionable_import_error(client, monkeypatch):
    real_import = builtins.__import__

    def no_openai(name, *args, **kwargs):
        if name == "openai" or name.startswith("openai."):
            raise ImportError("No module named 'openai'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_openai)

    with pytest.raises(ImportError, match=r"\[openai\]"):
        client.openai_client()
    with pytest.raises(ImportError, match=r"\[openai\]"):
        client.async_openai_client()


def _assert_rotating_auth(httpx_client) -> None:
    """The wired httpx client must stamp a fresh bearer per request via auth_flow."""
    import httpx

    auth = httpx_client.auth
    assert isinstance(auth, httpx.Auth)
    request = httpx.Request("POST", "http://gw.test:4000/v1/chat/completions")
    next(auth.auth_flow(request))
    assert request.headers["Authorization"] == "Bearer test-token"  # from FakeCredentials


def test_openai_client_is_wired_to_gateway(client):
    openai = pytest.importorskip("openai")

    oai = client.openai_client(max_retries=0)

    assert isinstance(oai, openai.OpenAI)
    assert str(oai.base_url) == "http://gw.test:4000/v1/"  # gateway host, /v1 path
    assert oai.default_headers.get("x-org") == "2"
    assert oai.max_retries == 0  # caller kwargs pass through
    _assert_rotating_auth(oai._client)


def test_async_openai_client_is_wired_to_gateway(client):
    openai = pytest.importorskip("openai")

    aoai = client.async_openai_client()

    assert isinstance(aoai, openai.AsyncOpenAI)
    assert str(aoai.base_url) == "http://gw.test:4000/v1/"
    assert aoai.default_headers.get("x-org") == "2"
    _assert_rotating_auth(aoai._client)


def test_openai_clients_are_independent_instances(client):
    pytest.importorskip("openai")

    assert client.openai_client() is not client.openai_client()  # no cache: caller owns lifecycle
