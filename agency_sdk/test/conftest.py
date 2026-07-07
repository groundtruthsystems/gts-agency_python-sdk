"""Shared offline fixtures for client tests.

All unit tests run without network access: `stub_requests` monkeypatches the
`requests` entry points the SDK uses (`requests.request`, `requests.get`,
`requests.post`) with a recorder that captures every call and replays queued
canned responses.
"""

from dataclasses import dataclass, field
from typing import Any

import pytest
import requests


class StubResponse:
    """Minimal stand-in for `requests.Response`."""

    def __init__(
        self,
        json_data: Any = None,
        status_code: int = 200,
        content_bytes: bytes | None = None,
        text: str | None = None,
    ):
        self.status_code = status_code
        self._json = json_data
        if content_bytes is not None:
            self.content = content_bytes
        elif json_data is not None:
            self.content = b"json"  # truthy marker; clients only check truthiness
        else:
            self.content = b""
        self.text = text if text is not None else ""

    def json(self) -> Any:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)

    def iter_content(self, chunk_size: int = 8192):
        for i in range(0, len(self.content), chunk_size):
            yield self.content[i : i + chunk_size]

    def iter_lines(self, chunk_size: int = 512, decode_unicode: bool = False, delimiter=None):
        """Line-wise replay of `content` (SSE streaming tests); mirrors requests semantics."""
        data = self.content.decode() if decode_unicode else self.content
        yield from (data.split(delimiter) if delimiter is not None else data.splitlines())

    def close(self) -> None:
        self.closed = True  # streaming clients must close the response; tests assert this


@dataclass
class CapturedCall:
    method: str
    url: str
    kwargs: dict[str, Any] = field(default_factory=dict)


class RequestRecorder:
    """Captures calls to the requests library and replays queued responses."""

    def __init__(self):
        self.calls: list[CapturedCall] = []
        self._responses: list[StubResponse] = []

    def queue(
        self,
        json_data: Any = None,
        status_code: int = 200,
        content_bytes: bytes | None = None,
        text: str | None = None,
    ) -> StubResponse:
        response = StubResponse(json_data=json_data, status_code=status_code, content_bytes=content_bytes, text=text)
        self._responses.append(response)
        return response

    def __call__(self, method: str, url: str, **kwargs: Any) -> StubResponse:
        self.calls.append(CapturedCall(method=method, url=url, kwargs=kwargs))
        if not self._responses:
            raise AssertionError(f"No stub response queued for {method} {url}")
        return self._responses.pop(0)


class FakeCredentials:
    """Duck-typed CredentialsSupplier returning a static token, no network."""

    def bearer_token(self) -> str:
        return "test-token"


@pytest.fixture
def stub_requests(monkeypatch):
    recorder = RequestRecorder()
    monkeypatch.setattr(requests, "request", recorder)
    monkeypatch.setattr(requests, "get", lambda url, **kw: recorder(method="GET", url=url, **kw))
    monkeypatch.setattr(requests, "post", lambda url, **kw: recorder(method="POST", url=url, **kw))
    return recorder


@pytest.fixture
def fake_credentials():
    return FakeCredentials()
