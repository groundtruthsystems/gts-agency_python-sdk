"""OTLP tracing/logging bootstrap, authenticated via the SDK credentials.

Construct via :meth:`agency_sdk.client.AgencyClient.observability`, which binds the
client's shared :class:`~agency_sdk.credentials.CredentialsSupplier` and defaults
the OTLP host to the client ``base_url``.

Heavy OpenTelemetry/Langfuse imports happen inside the lifecycle methods
(``init``/``langfuse_client``), never at module import or object construction, so
this module imports cleanly without the ``[observability]`` extra installed.
"""

from __future__ import annotations

import base64
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
        logs_endpoint: str | None = None,
        traces_endpoint: str | None = None,
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
        self.logs_endpoint = logs_endpoint
        self.traces_endpoint = traces_endpoint
        self.extra_headers = extra_headers
        self.langfuse_public_key = langfuse_public_key
        self.langfuse_secret_key = langfuse_secret_key
        self.langfuse_host = langfuse_host or host
        self.logger = logger or logging.getLogger(__name__)

    # -- auth / header / endpoint helpers -------------------------------------

    def _safe_token(self) -> str | None:
        """Return a fresh bearer token from the shared credentials, or None.

        Never raises: a transient auth failure degrades to an untraced export
        rather than crashing the exporter, so observability stays best-effort.
        """
        try:
            return self.credentials.bearer_token()
        except Exception as exc:  # noqa: BLE001 - observability must not crash the caller
            self.logger.warning("Observability token fetch failed: %s", exc)
            return None

    def build_headers(self) -> dict[str, str]:
        """Static OTLP headers including the seed Authorization.

        Precedence: an explicit ``Authorization`` in ``extra_headers`` > a bearer
        token from the shared credentials > Langfuse Basic (base64 of
        ``public:secret``). When credentials are active, the per-request auth hook
        refreshes the header on every export; this static value is only the seed.
        ``x-org-id`` is always set.
        """
        headers: dict[str, str] = {}

        if self.extra_headers:
            for item in self.extra_headers.split(","):
                if "=" in item:
                    key, value = item.split("=", 1)
                    headers[key.strip()] = value.strip()

        if "Authorization" not in headers:
            token = self._safe_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"

        if "Authorization" not in headers and self.langfuse_public_key and self.langfuse_secret_key:
            auth_b64 = base64.b64encode(
                f"{self.langfuse_public_key}:{self.langfuse_secret_key}".encode("utf-8")
            ).decode("utf-8")
            headers["Authorization"] = f"Bearer {auth_b64}"

        headers["x-org-id"] = self.org_id
        return headers

    def _resolve_endpoint(self, explicit: str | None, path: str) -> str | None:
        """An explicit per-signal endpoint wins; else ``host`` + the signal path."""
        if explicit:
            return explicit
        if self.host:
            return self.host if self.host.endswith(path) else self.host.rstrip("/") + path
        return None
