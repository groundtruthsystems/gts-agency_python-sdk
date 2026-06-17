"""Phase 4: telemetry pipeline & log/trace correlation, verified offline.

Uses OpenTelemetry in-memory exporters (no network) to assert that ``init()``
builds real recording providers, that stdlib logs emitted inside an active span
carry that span's trace id, that the processor is selectable, and that exporter
failure degrades to ``init() -> None`` without side effects.
"""

import logging

import pytest

from opentelemetry.sdk._logs.export import (
    BatchLogRecordProcessor,
    InMemoryLogExporter,
    SimpleLogRecordProcessor,
)
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agency_sdk.observability.auth import BearerTokenAuth
from agency_sdk.observability.bootstrap import Observability

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


class _StaticCreds:
    def bearer_token(self) -> str:
        return "tok"


@pytest.fixture
def otel_isolation():
    """Undo init()'s global side effects (root handler + LoggingInstrumentor)."""
    root = logging.getLogger()
    handlers_before = list(root.handlers)
    level_before = root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers_before:
            root.removeHandler(handler)
    root.setLevel(level_before)
    try:
        from opentelemetry.instrumentation.logging import LoggingInstrumentor

        LoggingInstrumentor().uninstrument()
    except Exception:
        pass


def _obs_with_inmemory(monkeypatch, processor="simple"):
    obs = Observability(_StaticCreds(), "gts-test", host="http://cp.test", processor=processor)
    span_exporter = InMemorySpanExporter()
    log_exporter = InMemoryLogExporter()
    monkeypatch.setattr(obs, "make_span_exporter", lambda: span_exporter)
    monkeypatch.setattr(obs, "make_log_exporter", lambda: log_exporter)
    return obs, span_exporter, log_exporter


def test_init_builds_recording_providers(monkeypatch, otel_isolation):
    obs, span_exporter, _ = _obs_with_inmemory(monkeypatch)

    tracer = obs.init()

    assert tracer is not None
    with tracer.start_as_current_span("root") as span:
        ctx = span.get_span_context()
        assert ctx.trace_id != 0  # real recording provider, valid context
        assert span.is_recording()
    obs.shutdown()
    assert any(s.name == "root" for s in span_exporter.get_finished_spans())


def test_logs_carry_active_span_trace_id(monkeypatch, otel_isolation):
    obs, _, log_exporter = _obs_with_inmemory(monkeypatch)
    tracer = obs.init()
    logging.getLogger().setLevel(logging.DEBUG)

    with tracer.start_as_current_span("root") as span:
        trace_id = span.get_span_context().trace_id
        logging.getLogger("obs.test.correlation").warning("correlated-line")

    obs._logger_provider.force_flush()
    matched = [r for r in log_exporter.get_finished_logs() if r.log_record.body == "correlated-line"]
    obs.shutdown()

    assert matched, "log line was not exported"
    assert matched[0].log_record.trace_id == trace_id


def test_processor_choice_simple_default_and_batch():
    span_exp = InMemorySpanExporter()
    log_exp = InMemoryLogExporter()

    simple = Observability(_StaticCreds(), "s")  # default
    batch = Observability(_StaticCreds(), "s", processor="batch")

    assert isinstance(simple._make_span_processor(span_exp), SimpleSpanProcessor)
    assert isinstance(simple._make_log_processor(log_exp), SimpleLogRecordProcessor)

    batch_span = batch._make_span_processor(span_exp)
    batch_log = batch._make_log_processor(log_exp)
    assert isinstance(batch_span, BatchSpanProcessor)
    assert isinstance(batch_log, BatchLogRecordProcessor)
    batch_span.shutdown()  # stop the worker threads
    batch_log.shutdown()


def test_make_span_exporter_attaches_refreshing_auth():
    obs = Observability(_StaticCreds(), "gts-test", host="http://cp.test")

    exporter = obs.make_span_exporter()

    assert isinstance(exporter._session.auth, BearerTokenAuth)


def test_make_log_exporter_attaches_refreshing_auth():
    obs = Observability(_StaticCreds(), "gts-test", host="http://cp.test")

    exporter = obs.make_log_exporter()

    assert isinstance(exporter._session.auth, BearerTokenAuth)


def test_tracer_provider_property_reflects_lifecycle(monkeypatch, otel_isolation):
    obs, _, _ = _obs_with_inmemory(monkeypatch)

    assert obs.tracer_provider is None
    obs.init()
    assert obs.tracer_provider is not None
    obs.shutdown()


def test_init_returns_none_on_exporter_failure(monkeypatch, otel_isolation):
    obs = Observability(_StaticCreds(), "gts-test", host="http://cp.test")

    def boom():
        raise RuntimeError("exporter down")

    monkeypatch.setattr(obs, "make_log_exporter", boom)
    handlers_before = list(logging.getLogger().handlers)

    result = obs.init()

    assert result is None  # graceful degradation, no exception
    # No global side effects when setup fails.
    assert list(logging.getLogger().handlers) == handlers_before
