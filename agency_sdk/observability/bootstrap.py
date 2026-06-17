"""OTLP tracing/logging bootstrap, authenticated via the SDK credentials.

Construct via :meth:`agency_sdk.client.AgencyClient.observability`, which binds the
client's shared :class:`~agency_sdk.credentials.CredentialsSupplier` and defaults
the OTLP host to the client ``base_url``.

Heavy OpenTelemetry/Langfuse imports happen inside the lifecycle methods
(``init``/``langfuse_client``), never at module import or object construction, so
this module imports cleanly without the ``[observability]`` extra installed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agency_sdk.credentials import CredentialsSupplier

# Langfuse's OTLP signal paths; overridable for other OTLP backends.
DEFAULT_LOGS_PATH = "/api/public/otel/v1/logs"
DEFAULT_TRACES_PATH = "/api/public/otel/v1/traces"


class Observability:
    """Bootstraps OTLP log + trace export, authenticated via the SDK credentials.

    The per-request OTLP/Langfuse auth hooks read their bearer token from the
    shared ``CredentialsSupplier``, so one cached token serves both the API client
    and the telemetry exporters.
    """

    def __init__(
        self,
        credentials: CredentialsSupplier,
        service_name: str,
        service_version: str = "unknown-0",
        *,
        host: str | None = None,
        environment: str = "development",
        org_id: str = "2",
        processor: str = "simple",
        logs_path: str = DEFAULT_LOGS_PATH,
        traces_path: str = DEFAULT_TRACES_PATH,
        extra_headers: str | None = None,
        langfuse_public_key: str | None = None,
        langfuse_secret_key: str | None = None,
        langfuse_host: str | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.credentials = credentials
        self.service_name = service_name
        self.service_version = service_version
        self.host = host
        self.environment = environment
        self.org_id = org_id
        self.processor = processor
        self.logs_path = logs_path
        self.traces_path = traces_path
        self.extra_headers = extra_headers
        self.langfuse_public_key = langfuse_public_key
        self.langfuse_secret_key = langfuse_secret_key
        self.langfuse_host = langfuse_host or host
        self.logger = logger or logging.getLogger(__name__)
