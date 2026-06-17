"""OTLP tracing/logging bootstrap, authenticated via the SDK credentials.

Construct via :meth:`agency_sdk.client.AgencyClient.observability`, which binds the
client's shared :class:`~agency_sdk.credentials.CredentialsSupplier` and defaults
the OTLP host to the client ``base_url``.

Heavy OpenTelemetry/Langfuse imports happen inside the lifecycle methods
(``init``/``langfuse_client``), never at module import or object construction, so
this module imports cleanly without the ``[observability]`` extra installed.
"""

from __future__ import annotations

import atexit
import base64
import contextlib
import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from agency_sdk.observability.auth import BearerTokenAuth

if TYPE_CHECKING:
    from agency_sdk.credentials import CredentialsSupplier
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import LogRecordProcessor
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SpanProcessor
    from opentelemetry.trace import Span, Tracer

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

        self._tracer_provider: TracerProvider | None = None
        self._logger_provider: LoggerProvider | None = None
        self._tracer: Tracer | None = None
        self._log_handler: logging.Handler | None = None

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

    # -- exporter / processor construction ------------------------------------

    def _exporter_kwargs(self, endpoint: str | None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if endpoint:
            kwargs["endpoint"] = endpoint
        headers = self.build_headers()
        if headers:
            kwargs["headers"] = headers
        return kwargs

    def _attach_dynamic_auth(self, exporter: Any) -> None:
        """Refresh the bearer token on every export for this exporter (Mechanism 2).

        OTLP HTTP exporters freeze their static headers into a ``requests.Session``;
        ``requests`` calls ``session.auth`` on every request, so routing the token
        through :class:`BearerTokenAuth` keeps a long-running process from sending an
        expired token.
        """
        session = getattr(exporter, "_session", None)
        if session is not None:
            session.auth = BearerTokenAuth(self._safe_token)

    def make_span_exporter(self) -> OTLPSpanExporter:
        """An OTLP span exporter for the traces endpoint, with refreshing auth."""
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        endpoint = self._resolve_endpoint(self.traces_endpoint, self.traces_path)
        exporter = OTLPSpanExporter(**self._exporter_kwargs(endpoint))
        self._attach_dynamic_auth(exporter)
        return exporter

    def make_log_exporter(self) -> OTLPLogExporter:
        """An OTLP log exporter for the logs endpoint, with refreshing auth."""
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

        endpoint = self._resolve_endpoint(self.logs_endpoint, self.logs_path)
        exporter = OTLPLogExporter(**self._exporter_kwargs(endpoint))
        self._attach_dynamic_auth(exporter)
        return exporter

    def _make_span_processor(self, exporter: Any) -> SpanProcessor:
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

        if self.processor == "batch":
            return BatchSpanProcessor(exporter)
        return SimpleSpanProcessor(exporter)

    def _make_log_processor(self, exporter: Any) -> LogRecordProcessor:
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, SimpleLogRecordProcessor

        if self.processor == "batch":
            return BatchLogRecordProcessor(exporter)
        return SimpleLogRecordProcessor(exporter)

    # -- lifecycle ------------------------------------------------------------

    @property
    def tracer_provider(self) -> TracerProvider | None:
        return self._tracer_provider

    def init(self) -> Tracer | None:
        """Configure OTLP log + trace export and return a tracer for manual spans.

        Builds a real recording ``TracerProvider`` and a ``LoggerProvider`` (both
        held explicitly, not registered globally — correlation is driven by the OTel
        context), bridges stdlib logging to OTLP via ``LoggingInstrumentor`` + a root
        ``LoggingHandler`` so log records carry the active span's trace/span ids, and
        returns the tracer. Returns ``None`` if exporter setup fails so the caller
        keeps running untraced rather than crashing.
        """
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider

        resource = Resource.create(
            {
                "service.name": self.service_name,
                "service.version": self.service_version,
                "deployment.environment": self.environment,
            }
        )

        # Build exporters and providers first; any failure leaves us fully untraced
        # with no global side effects (no instrumentation, no root handler).
        try:
            log_exporter = self.make_log_exporter()
            span_exporter = self.make_span_exporter()
            logger_provider = LoggerProvider(resource=resource)
            logger_provider.add_log_record_processor(self._make_log_processor(log_exporter))
            tracer_provider = TracerProvider(resource=resource)
            tracer_provider.add_span_processor(self._make_span_processor(span_exporter))
        except Exception as exc:  # noqa: BLE001 - observability must not crash the caller
            self.logger.warning("Failed to initialize OTLP exporters: %s", exc)
            return None

        self._logger_provider = logger_provider
        self._tracer_provider = tracer_provider
        atexit.register(logger_provider.shutdown)
        atexit.register(tracer_provider.shutdown)

        # LoggingInstrumentor injects trace/span ids into stdlib LogRecords; the
        # LoggingHandler exports those records to the LoggerProvider. Both read the
        # active span from the OTel context at emit time (so a span must be active).
        LoggingInstrumentor().instrument()
        handler = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
        logging.getLogger().addHandler(handler)
        self._log_handler = handler

        self._tracer = tracer_provider.get_tracer(self.service_name)
        return self._tracer

    @contextlib.contextmanager
    def agent_run(self, name: str, **attributes: Any) -> Iterator[Span | None]:
        """Open a recording root span for an agent run (Mechanism 5).

        Encodes the "open a root span first" rule so logs and child spans created
        inside the block correlate under one trace. Degrades to a no-op yielding
        ``None`` when tracing is off (``init`` not called or returned ``None``), so
        the same ``with`` block runs untraced rather than crashing.
        """
        if self._tracer is None:
            yield None
            return
        with self._tracer.start_as_current_span(name) as span:
            for key, value in attributes.items():
                span.set_attribute(key, value)
            yield span

    def shutdown(self) -> None:
        """Detach the log handler and flush/close both providers. Idempotent."""
        if self._log_handler is not None:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler = None
        if self._logger_provider is not None:
            self._logger_provider.shutdown()
        if self._tracer_provider is not None:
            self._tracer_provider.shutdown()
