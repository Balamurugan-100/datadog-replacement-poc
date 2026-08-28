"""
OpenTelemetry SDK setup for Django applications.

This module initializes the OTel tracer provider, span processors, and instrumentors.
All configuration is driven by environment variables — zero hardcoded values.

Environment Variables:
    OTEL_ENABLED                  : Enable/disable tracing entirely (default: "true")
    OTEL_SERVICE_NAME             : Service name for traces (default: "django")
    OTEL_ENVIRONMENT              : Deployment environment label (default: "development")
    OTEL_EXPORTER_OTLP_ENDPOINT   : Collector gRPC endpoint (default: "http://localhost:4317")
    OTEL_BATCH_DELAY_MS           : BatchSpanProcessor flush interval (default: "2000")
    OTEL_BATCH_MAX_SIZE           : Max spans per export batch (default: "256")
    OTEL_BATCH_QUEUE_SIZE         : Max spans queued in memory (default: "2048")
    OTEL_FLUSH_TIMEOUT_MS         : Shutdown flush timeout (default: "5000")
"""

import logging
import os

from opentelemetry import trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

logger = logging.getLogger("django_otel_sdk.setup")

_initialized = False


def _is_otel_enabled():
    return os.getenv("OTEL_ENABLED", "true").lower() in ("true", "1", "yes")


def _init_tracer_provider():
    telemetry_resource = Resource.create(
        {
            SERVICE_NAME: os.getenv("OTEL_SERVICE_NAME", "django"),
            "deployment.environment": os.getenv("OTEL_ENVIRONMENT", "development"),
            "application": os.getenv("OTEL_APPLICATION", "django-otel"),
        }
    )

    tracer_provider_instance = TracerProvider(resource=telemetry_resource)

    from django_otel_sdk.cache import RedisSpanProcessor
    from django_otel_sdk.db import PostgresSpanProcessor

    tracer_provider_instance.add_span_processor(PostgresSpanProcessor())
    tracer_provider_instance.add_span_processor(RedisSpanProcessor())

    otlp_exporter_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    otlp_span_exporter = OTLPSpanExporter(endpoint=otlp_exporter_endpoint, insecure=True)
    tracer_provider_instance.add_span_processor(
        BatchSpanProcessor(
            otlp_span_exporter,
            schedule_delay_millis=int(os.getenv("OTEL_BATCH_DELAY_MS", "2000")),
            max_export_batch_size=int(os.getenv("OTEL_BATCH_MAX_SIZE", "256")),
            max_queue_size=int(os.getenv("OTEL_BATCH_QUEUE_SIZE", "2048")),
        )
    )

    trace.set_tracer_provider(tracer_provider_instance)

    global_composite_propagator = CompositePropagator(
        [
            TraceContextTextMapPropagator(),
            W3CBaggagePropagator(),
        ]
    )
    set_global_textmap(global_composite_propagator)


def _init_instrumentors():
    from opentelemetry.instrumentation.django import DjangoInstrumentor
    from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.instrumentation.requests import RequestsInstrumentor

    from django_otel_sdk.cache import instrument_django_redis
    from django_otel_sdk.db import (
        instrument_django_db_aliases,
        instrument_psycopg2_rowcount,
    )
    from django_otel_sdk.django import (
        django_response_hook,
        instrument_middleware,
        instrument_template_render,
    )

    instrument_django_db_aliases()
    instrument_psycopg2_rowcount()
    instrument_django_redis()
    instrument_middleware()
    instrument_template_render()

    DjangoInstrumentor().instrument(
        response_hook=django_response_hook,
        is_middleware_instrumentation_enabled=True,
    )
    Psycopg2Instrumentor().instrument()
    RedisInstrumentor().instrument()
    RequestsInstrumentor().instrument()


def init_tracing(*args, **kwargs):
    """Initialize OpenTelemetry tracing. Safe to call multiple times — only runs once.

    Delegates to otel_sdk.init_tracing when available (framework-agnostic SDK).
    Falls back to legacy Django-only init when python-otel-sdk is not installed.
    """
    try:
        from otel_sdk.sdk import init_tracing as generic_init

        # Preserve legacy default service_name="django" when using Django shim
        if "service_name" not in kwargs and not args and not os.getenv("OTEL_SERVICE_NAME"):
            kwargs["service_name"] = "django"
        return generic_init(*args, frameworks=kwargs.pop("frameworks", ["django"]), **kwargs)
    except ImportError:
        pass

    global _initialized
    if _initialized:
        logger.debug("OTel tracing already initialized, skipping.")
        return

    if not _is_otel_enabled():
        logger.info("OTel tracing is disabled (OTEL_ENABLED=%s).", os.getenv("OTEL_ENABLED"))
        _initialized = True
        return

    _init_tracer_provider()
    _init_instrumentors()
    _initialized = True
    logger.info(
        "OTel tracing initialized: service=%s, endpoint=%s",
        os.getenv("OTEL_SERVICE_NAME", "django"),
        os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
    )
