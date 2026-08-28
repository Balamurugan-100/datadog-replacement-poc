"""
otel_sdk — framework-agnostic OpenTelemetry SDK.

Drop-in replacement for django_otel_sdk with multi-framework support
(Django, FastAPI, Flask, Starlette/ASGI) + manual instrumentation helpers.

Quickstart:
    from otel_sdk import init_tracing
    init_tracing()  # reads OTEL_* env vars

    # FastAPI
    from fastapi import FastAPI
    app = FastAPI()
    init_tracing(service_name="my-api", frameworks=["fastapi"], app=app)

    # Django — add to MIDDLEWARE: otel_sdk.frameworks.django.ViewTracingMiddleware
    # and call init_tracing() in gunicorn post_fork or settings.py

    # Manual spans
    from otel_sdk import traced, span
    @traced
    def my_func(): ...
    with span("custom.work"):
        ...

    # Shutdown (gunicorn worker_exit, lifespan, etc.)
    from otel_sdk import shutdown_tracing
"""
from otel_sdk.core.config import OtelConfig
from otel_sdk.core.tracer import init_tracer_provider, shutdown_tracer_provider
from otel_sdk.decorators import add_span_attributes, set_span_error, span, trace_function, trace_method, traced
from otel_sdk.frameworks.django import ViewTracingMiddleware
from otel_sdk.sdk import get_config, init_tracing, is_initialized, shutdown_tracing

__all__ = [
    # Core
    "init_tracing",
    "shutdown_tracing",
    "is_initialized",
    "get_config",
    "OtelConfig",
    "init_tracer_provider",
    "shutdown_tracer_provider",
    # Django compat
    "ViewTracingMiddleware",
    # Manual
    "traced",
    "trace_method",
    "trace_function",
    "span",
    "add_span_attributes",
    "set_span_error",
]
