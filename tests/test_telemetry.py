import logging

import pytest
from config import telemetry
from config.logging import TraceContextFilter


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(telemetry, "_configured", False)
    for name in (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_SDK_DISABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class TestEnabled:
    def test_off_without_an_endpoint(self, fresh):
        assert not telemetry.is_enabled()
        assert telemetry.configure("test") is False

    def test_on_with_an_endpoint(self, fresh):
        fresh.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel:4318")
        assert telemetry.is_enabled()

    def test_traces_endpoint_alone(self, fresh):
        fresh.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://otel:4318")
        assert telemetry.is_enabled()

    def test_can_be_disabled(self, fresh):
        fresh.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel:4318")
        fresh.setenv("OTEL_SDK_DISABLED", "true")
        assert not telemetry.is_enabled()

    def test_only_once(self, fresh):
        fresh.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel:4318")
        calls = []
        fresh.setattr(telemetry, "_instrument", lambda: calls.append(1))
        fresh.setattr(
            "opentelemetry.trace.set_tracer_provider", lambda provider: None
        )
        assert telemetry.configure("test") is True
        assert telemetry.configure("test") is False
        assert calls == [1]


class TestTraceContextFilter:
    def record(self, **extra):
        record = logging.LogRecord("x", logging.INFO, "x", 1, "msg", (), None)
        for key, value in extra.items():
            setattr(record, key, value)
        return record

    def test_fills_in_missing_ids(self):
        record = self.record()
        assert TraceContextFilter().filter(record)
        assert (record.otelTraceID, record.otelSpanID) == ("0", "0")

    def test_keeps_real_ids(self):
        record = self.record(otelTraceID="abc", otelSpanID="def")
        TraceContextFilter().filter(record)
        assert (record.otelTraceID, record.otelSpanID) == ("abc", "def")
