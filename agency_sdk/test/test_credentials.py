"""Tests for CredentialsSupplier, including the observability early-refresh buffer.

The buffer makes a token count as expired shortly before its real ``exp`` so the
observability OTLP per-request hook never stamps a token that expires in transit.
"""

import time

import jwt

from agency_sdk.credentials import CredentialsSupplier


# 32-byte key keeps PyJWT from emitting an InsecureKeyLengthWarning; the value is
# irrelevant since the SDK decodes without signature verification.
_TEST_KEY = "x" * 32


def _jwt_expiring_in(seconds: int) -> str:
    return jwt.encode({"exp": int(time.time()) + seconds}, _TEST_KEY, algorithm="HS256")


def test_token_within_refresh_buffer_counts_as_expired():
    cs = CredentialsSupplier("http://auth", "id", "secret", refresh_buffer=30.0)
    cs._cached_token = _jwt_expiring_in(10)  # inside the 30s buffer

    assert cs._is_token_expired() is True


def test_token_beyond_refresh_buffer_is_valid():
    cs = CredentialsSupplier("http://auth", "id", "secret", refresh_buffer=30.0)
    cs._cached_token = _jwt_expiring_in(120)  # well beyond the buffer

    assert cs._is_token_expired() is False


def test_no_cached_token_is_expired():
    cs = CredentialsSupplier("http://auth", "id", "secret")
    assert cs._is_token_expired() is True


def test_default_refresh_buffer_is_applied():
    # exp 5s out should be considered expired under the default (30s) buffer.
    cs = CredentialsSupplier("http://auth", "id", "secret")
    cs._cached_token = _jwt_expiring_in(5)

    assert cs._is_token_expired() is True


def test_malformed_cached_token_is_treated_as_expired():
    cs = CredentialsSupplier("http://auth", "id", "secret")
    cs._cached_token = "not-a-jwt"

    assert cs._is_token_expired() is True


def test_bearer_token_fetches_then_caches(stub_requests):
    token = _jwt_expiring_in(3600)
    stub_requests.queue(json_data={"access_token": token})
    cs = CredentialsSupplier("http://auth/token/", "id", "secret")

    first = cs.bearer_token()
    second = cs.bearer_token()  # served from cache; no second response queued

    assert first == token
    assert second == token
    assert len(stub_requests.calls) == 1
