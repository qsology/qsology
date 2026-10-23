"""OpenTelemetry tracing

Set up once in each process that does work: gunicorn workers after they
fork, celery worker processes as they start and celery beat. Nothing is set
up unless OTEL_EXPORTER_OTLP_ENDPOINT is, so development and tests don't
need a collector.

Traces carry on from the gateway's traceparent header, through Django, into
the celery tasks it queues, and out to PostgreSQL, the cache, S3 and HTTP
calls, so a request can be followed end to end.

Metrics aren't sent this way: the web app's are scraped (config.metrics),
celery's come from celery-exporter.

The usual OTEL_* environment variables apply, e.g. OTEL_SERVICE_NAME,
OTEL_RESOURCE_ATTRIBUTES, OTEL_TRACES_SAMPLER and
OTEL_EXPORTER_OTLP_PROTOCOL"""

import logging
import os
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_configured = False


def is_enabled() -> bool:
    return (
        bool(
            os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
            or os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        )
        and os.environ.get("OTEL_SDK_DISABLED", "").lower() != "true"
    )


def configure(service_name: str | None = None) -> bool:
    """Start exporting traces, returning whether it did

    Safe to call more than once, only the first call does anything"""
    global _configured
    with _lock:
        if _configured or not is_enabled():
            return False
        _configured = True

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.sdk.resources import SERVICE_INSTANCE_ID, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    attributes = {
        # Which process a span came from
        SERVICE_INSTANCE_ID: f"{os.environ.get('HOSTNAME', 'local')}-"
        f"{os.getpid()}",
    }
    if service_name and not os.environ.get("OTEL_SERVICE_NAME"):
        attributes["service.name"] = service_name
    resource = Resource.create(attributes)

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer_provider)

    _instrument()
    logger.info("OpenTelemetry exporting as %s", resource.attributes)
    return True


def _instrument() -> None:
    from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
    from opentelemetry.instrumentation.celery import CeleryInstrumentor
    from opentelemetry.instrumentation.django import DjangoInstrumentor
    from opentelemetry.instrumentation.logging import LoggingInstrumentor
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.instrumentation.requests import RequestsInstrumentor

    # Health checks would drown out everything else
    DjangoInstrumentor().instrument(excluded_urls="healthz,readyz")
    CeleryInstrumentor().instrument()
    PsycopgInstrumentor().instrument(enable_commenter=False)
    RedisInstrumentor().instrument()
    RequestsInstrumentor().instrument()
    BotocoreInstrumentor().instrument()
    # Puts the trace and span ids on log records, for the log format
    LoggingInstrumentor().instrument(set_logging_format=False)
