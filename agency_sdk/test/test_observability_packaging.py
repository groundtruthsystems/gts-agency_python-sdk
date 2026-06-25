"""Phase 2: packaging & lazy-import behaviour for the observability module.

These tests guarantee the optional ``[observability]`` extra never leaks into the
SDK's lean core: importing ``agency_sdk`` and constructing ``AgencyClient`` must
not pull OpenTelemetry, and requesting observability without the extra installed
must fail with a clear, actionable message naming the extra.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from agency_sdk.client import AgencyClient

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_import_agency_sdk_is_lazy_wrt_opentelemetry():
    """Importing the SDK core (in a fresh interpreter) must not import OpenTelemetry."""
    code = (
        "import sys, agency_sdk, agency_sdk.client\n"
        "from agency_sdk.client import AgencyClient\n"
        "leaked = [m for m in sys.modules if m == 'opentelemetry' or m.startswith('opentelemetry.')]\n"
        "assert not leaked, leaked\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("OK")


def test_observability_raises_clear_error_when_extra_missing(monkeypatch, fake_credentials):
    """Without the extra, ``observability()`` raises an ImportError naming the extra."""
    # Force the optional deps to look absent regardless of the dev environment.
    monkeypatch.setitem(sys.modules, "opentelemetry", None)
    monkeypatch.setitem(sys.modules, "opentelemetry.sdk", None)

    client = AgencyClient(token_supplier=fake_credentials, base_url="http://cp.test")

    with pytest.raises(ImportError) as exc_info:
        client.observability("gts-test")

    message = str(exc_info.value)
    assert "observability" in message.lower()
    assert "pip install" in message
    assert "[observability]" in message


def test_observability_returns_bound_cached_instance(fake_credentials):
    """With deps present, ``observability()`` returns an instance bound to the
    shared credentials, defaulting the host to the client ``base_url``, and caches it."""
    from agency_sdk.observability import Observability

    client = AgencyClient(token_supplier=fake_credentials, base_url="http://cp.test/")

    obs = client.observability("gts-test", "1.2.3")

    assert isinstance(obs, Observability)
    assert obs.credentials is fake_credentials
    assert obs.service_name == "gts-test"
    assert obs.service_version == "1.2.3"
    assert obs.host == "http://cp.test"  # defaults to (rstripped) client base_url
    assert client.observability("gts-test") is obs  # idempotent / cached


def test_observability_host_is_overridable(fake_credentials):
    client = AgencyClient(token_supplier=fake_credentials, base_url="http://cp.test")

    obs = client.observability("gts-test", host="http://otel.other:4318")

    assert obs.host == "http://otel.other:4318"
