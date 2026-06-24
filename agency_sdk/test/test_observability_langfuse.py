"""Phase 6: the Langfuse client helper.

``langfuse_client()`` returns None when the project keys or the langfuse package
are absent, and otherwise builds a Langfuse client authenticated through the same
refreshing bearer hook and the shared OTLP span exporter. The langfuse package is
stubbed so the test never touches the network.
"""

import sys
import types

import httpx

from agency_sdk.observability.bootstrap import Observability, TelemetryConfig


class _StaticCreds:
    def bearer_token(self) -> str:
        return "tok"


def test_langfuse_client_none_without_keys():
    obs = Observability(_StaticCreds(), "gts-x", config=TelemetryConfig(host="http://cp.test"))  # no langfuse keys
    assert obs.langfuse_client() is None


def test_langfuse_client_none_when_package_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "langfuse", None)  # force ImportError
    obs = Observability(
        _StaticCreds(),
        "gts-x",
        config=TelemetryConfig(
            host="http://cp.test",
            langfuse_public_key="pk",
            langfuse_secret_key="sk",
        ),
    )
    assert obs.langfuse_client() is None


def test_langfuse_client_built_with_bearer_auth_and_span_exporter(monkeypatch):
    captured: dict = {}

    class FakeLangfuse:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    fake_module = types.ModuleType("langfuse")
    fake_module.Langfuse = FakeLangfuse  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)

    obs = Observability(
        _StaticCreds(),
        "gts-x",
        config=TelemetryConfig(
            host="http://cp.test",
            langfuse_public_key="pk",
            langfuse_secret_key="sk",
        ),
    )

    client = obs.langfuse_client()

    assert isinstance(client, FakeLangfuse)
    assert captured["public_key"] == "pk"
    assert captured["secret_key"] == "sk"
    assert captured["host"] == "http://cp.test"  # langfuse_host defaults to host
    assert captured["span_exporter"] is not None

    httpx_client = captured["httpx_client"]
    assert isinstance(httpx_client, httpx.Client)
    # The transport auth is our refreshing bearer hook routed through _safe_token.
    request = type("R", (), {"headers": {}})()
    list(httpx_client._auth.auth_flow(request))
    assert request.headers["Authorization"] == "Bearer tok"
    httpx_client.close()
