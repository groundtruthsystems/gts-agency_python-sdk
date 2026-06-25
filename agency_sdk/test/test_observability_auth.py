"""Phase 3: per-request bearer hooks, header precedence, endpoint resolution.

All offline. The auth hooks must stamp a *fresh* token on every call (so a
long-running export never sends an expired one), and ``build_headers`` must
resolve the Authorization header in the documented precedence order.
"""

import base64

import pytest

from agency_sdk.observability.auth import BearerTokenAuth, make_httpx_bearer_auth
from agency_sdk.observability.bootstrap import (
    DEFAULT_LOGS_PATH,
    DEFAULT_TRACES_PATH,
    Observability,
    TelemetryConfig,
)


class _FakeRequest:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}


class _StaticCreds:
    """Duck-typed CredentialsSupplier; raises to simulate an unavailable token."""

    def __init__(self, token: str | None) -> None:
        self._token = token

    def bearer_token(self) -> str:
        if self._token is None:
            raise RuntimeError("no auth configured")
        return self._token


def _obs(creds: object, **kw: object) -> Observability:
    return Observability(creds, "gts-test", config=TelemetryConfig(**kw))  # type: ignore[arg-type]


# -- requests hook ------------------------------------------------------------


def test_requests_bearer_auth_stamps_fresh_token_each_call():
    tokens = iter(["tok-1", "tok-2"])
    auth = BearerTokenAuth(lambda: next(tokens))

    r1, r2 = _FakeRequest(), _FakeRequest()
    auth(r1)
    auth(r2)

    assert r1.headers["Authorization"] == "Bearer tok-1"
    assert r2.headers["Authorization"] == "Bearer tok-2"


def test_requests_bearer_auth_skips_when_no_token():
    auth = BearerTokenAuth(lambda: None)
    r = _FakeRequest()

    auth(r)

    assert "Authorization" not in r.headers


# -- httpx hook ---------------------------------------------------------------


def test_httpx_bearer_auth_mirrors_requests_hook():
    auth = make_httpx_bearer_auth(lambda: "tok-x")
    req = _FakeRequest()

    yielded = list(auth.auth_flow(req))

    assert len(yielded) == 1
    assert yielded[0].headers["Authorization"] == "Bearer tok-x"


# -- header precedence chain --------------------------------------------------


def test_build_headers_uses_bearer_token():
    headers = _obs(_StaticCreds("abc")).build_headers()
    assert headers["Authorization"] == "Bearer abc"
    assert headers["x-org-id"] == "2"


def test_build_headers_explicit_header_wins():
    obs = _obs(_StaticCreds("abc"), extra_headers="Authorization=Bearer explicit,X-Foo=bar")
    headers = obs.build_headers()
    assert headers["Authorization"] == "Bearer explicit"
    assert headers["X-Foo"] == "bar"


def test_build_headers_falls_back_to_langfuse_basic():
    obs = _obs(_StaticCreds(None), langfuse_public_key="pk", langfuse_secret_key="sk")
    headers = obs.build_headers()
    expected = "Bearer " + base64.b64encode(b"pk:sk").decode("utf-8")
    assert headers["Authorization"] == expected


def test_build_headers_org_id_always_set():
    assert _obs(_StaticCreds("abc"), org_id="7").build_headers()["x-org-id"] == "7"


# -- endpoint resolution ------------------------------------------------------


def test_resolve_endpoint_explicit_wins():
    obs = _obs(_StaticCreds("t"), host="http://h:1")
    assert obs._resolve_endpoint("http://explicit/x", DEFAULT_TRACES_PATH) == "http://explicit/x"


def test_resolve_endpoint_host_plus_path():
    obs = _obs(_StaticCreds("t"), host="http://h:1/")
    assert obs._resolve_endpoint(None, DEFAULT_TRACES_PATH) == "http://h:1" + DEFAULT_TRACES_PATH


def test_resolve_endpoint_host_already_has_path():
    obs = _obs(_StaticCreds("t"), host="http://h:1" + DEFAULT_LOGS_PATH)
    assert obs._resolve_endpoint(None, DEFAULT_LOGS_PATH) == "http://h:1" + DEFAULT_LOGS_PATH


def test_resolve_endpoint_none_without_host():
    assert _obs(_StaticCreds("t"), host=None)._resolve_endpoint(None, DEFAULT_TRACES_PATH) is None
