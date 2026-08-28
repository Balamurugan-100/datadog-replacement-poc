"""
Starlette / generic ASGI instrumentation adapter.
Useful for FastAPI without the FastAPI-specific hooks, or pure Starlette apps.
"""
import logging

logger = logging.getLogger("otel_sdk.frameworks.starlette")


def instrument_starlette(app):
    try:
        from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
    except ImportError as e:
        raise ImportError(
            "opentelemetry-instrumentation-asgi required. pip install 'python-otel-sdk[asgi]'"
        ) from e

    # Wrap app if not already wrapped
    if isinstance(app, OpenTelemetryMiddleware):
        return app
    wrapped = OpenTelemetryMiddleware(app)
    logger.info("Starlette/ASGI instrumentation enabled")
    return wrapped


def instrument_asgi(app):
    return instrument_starlette(app)
