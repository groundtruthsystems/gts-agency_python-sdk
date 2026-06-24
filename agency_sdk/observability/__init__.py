"""Optional OpenTelemetry tracing/logging support for the Agency SDK.

The heavy OpenTelemetry/Langfuse dependencies live behind the ``[observability]``
extra and are imported lazily, so importing :mod:`agency_sdk` and constructing
:class:`agency_sdk.client.AgencyClient` never require them. Enable the feature via
``AgencyClient.observability(...)``::

    client = AgencyClient(token_supplier=creds, base_url="http://localhost:13001")
    obs = client.observability("gts-myagent")
    tracer = obs.init()
    with obs.agent_run("agent.myagent", correlation_id=cid):
        ...

Install the extra with::

    pip install gts-agency-python-sdk[observability]
"""

from agency_sdk.observability.auth import BearerTokenAuth, make_httpx_bearer_auth
from agency_sdk.observability.bootstrap import (
    DEFAULT_LOGS_PATH,
    DEFAULT_TRACES_PATH,
    Observability,
    TelemetryConfig,
)

_INSTALL_HINT = (
    "Observability support requires the optional dependency group. Install it with:\n"
    "    pip install gts-agency-python-sdk[observability]"
)


class ObservabilityNotInstalled(ImportError):
    """Raised when observability is requested but the ``[observability]`` extra is absent."""


def require_observability_deps() -> None:
    """Verify the optional OpenTelemetry deps are importable, else raise a clear error.

    Called at the ``AgencyClient.observability(...)`` entry point so a missing
    extra fails fast with an actionable message rather than deep inside export.
    """
    try:
        import opentelemetry.sdk  # noqa: F401
        import opentelemetry.exporter.otlp.proto.http.trace_exporter  # noqa: F401
        import opentelemetry.instrumentation.logging  # noqa: F401
    except ImportError as exc:
        raise ObservabilityNotInstalled(_INSTALL_HINT) from exc


__all__ = [
    "Observability",
    "TelemetryConfig",
    "ObservabilityNotInstalled",
    "require_observability_deps",
    "BearerTokenAuth",
    "make_httpx_bearer_auth",
    "DEFAULT_LOGS_PATH",
    "DEFAULT_TRACES_PATH",
]
