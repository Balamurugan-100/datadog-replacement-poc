from __future__ import annotations
"""
Framework-agnostic TracerProvider + propagator + exporter setup.

This is the ONLY place that touches TracerProvider.  Framework adapters
must never create their own provider.
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

from otel_sdk.core.config import OtelConfig
from otel_sdk.processors.cache import RedisSpanProcessor
from otel_sdk.processors.db import PostgresSpanProcessor

logger = logging.getLogger("otel_sdk.core.tracer")


def init_tracer_provider(config: OtelConfig) -> TracerProvider:
    # If provider already set (e.g. pytest re-init, or worker fork reuses module),
    # reuse existing provider instead of attempting to override — OTel forbids double set.
    existing = trace.get_tracer_provider()
    if isinstance(existing, TracerProvider):
        logger.debug("TracerProvider already set, reusing existing provider")
        return existing

    resource = Resource.create(
        {
            SERVICE_NAME: config.service_name,
            "deployment.environment": config.environment,
            "application": config.application,
        }
    )

    provider = TracerProvider(resource=resource)

    # Generic span processors — safe without Django
    provider.add_span_processor(PostgresSpanProcessor())
    provider.add_span_processor(RedisSpanProcessor())

    exporter = OTLPSpanExporter(endpoint=config.otlp_endpoint, insecure=True)
    provider.add_span_processor(
        BatchSpanProcessor(
            exporter,
            schedule_delay_millis=config.batch_delay_ms,
            max_export_batch_size=config.batch_max_size,
            max_queue_size=config.batch_queue_size,
        )
    )

    try:
        trace.set_tracer_provider(provider)
    except Exception as e:
        # OTel raises if provider already set via set_tracer_provider race
        logger.debug("set_tracer_provider failed (already set): %s", e)

    propagator = CompositePropagator(
        [TraceContextTextMapPropagator(), W3CBaggagePropagator()]
    )
    set_global_textmap(propagator)

    logger.info(
        "OTel tracer initialized: service=%s env=%s endpoint=%s",
        config.service_name,
        config.environment,
        config.otlp_endpoint,
    )
    return provider


def shutdown_tracer_provider(timeout_ms: int | None = None):
    try:
        provider = trace.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            ms = timeout_ms or int(os.getenv("OTEL_FLUSH_TIMEOUT_MS", "5000"))
            try:
                provider.force_flush(timeout_millis=ms)
            except Exception:
                pass
        if hasattr(provider, "shutdown"):
            try:
                provider.shutdown()
            except Exception:
                pass
        # Reset global so a subsequent init_tracing() in same process (tests)
        # can create a fresh provider — production workers never re-init after exit.
        try:
            import opentelemetry.trace as _ot

            if isinstance(provider, TracerProvider):
                _ot._TRACER_PROVIDER = None
                if hasattr(_ot, "_TRACER_PROVIDER_SET_ONCE"):
                    _ot._TRACER_PROVIDER_SET_ONCE._done = False
        except Exception:
            pass
    except Exception:
        logger.debug("Error during tracer shutdown", exc_info=True)
