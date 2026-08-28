"""
FastAPI instrumentation adapter.

Usage:
    from fastapi import FastAPI
    from otel_sdk import init_tracing

    app = FastAPI()
    init_tracing(service_name="my-fastapi-service", frameworks=["fastapi"], app=app)

    # or two-step:
    from otel_sdk.frameworks.fastapi import instrument_fastapi
    init_tracing(service_name="my-service")
    instrument_fastapi(app)

The adapter enriches the server span with http.route / http.method /
http.status_code similar to the Django response_hook and provides
per-route view spans if desired.
"""
import logging

from opentelemetry import trace
from opentelemetry.semconv.trace import SpanAttributes

logger = logging.getLogger("otel_sdk.frameworks.fastapi")

_tracer = trace.get_tracer("fastapi.middleware")


def _fastapi_request_hook(span, scope):
    if span and span.is_recording():
        # scope is ASGI scope dict
        route = scope.get("route")
        if route and hasattr(route, "path_format"):
            span.set_attribute(SpanAttributes.HTTP_ROUTE, route.path_format)
        method = scope.get("method", "")
        if method:
            span.set_attribute(SpanAttributes.HTTP_METHOD, method)
        path = scope.get("path", "")
        if path:
            # keep low-cardinality route if available, else path
            if not span.attributes or "http.route" not in span.attributes:
                span.set_attribute("http.route", getattr(route, "path_format", path) if route else path)


def _fastapi_response_hook(span, scope, message):
    if span and span.is_recording():
        status = message.get("status", 200) if isinstance(message, dict) else 200
        span.set_attribute(SpanAttributes.HTTP_STATUS_CODE, status)
        if status >= 400:
            span.set_status(trace.Status(trace.StatusCode.ERROR, f"HTTP {status}"))


def instrument_fastapi(app, request_hook=None, response_hook=None, enable_view_spans: bool = True):
    """
    Instrument a FastAPI app.  Must be called after init_tracing().

    Args:
        app: FastAPI instance
        request_hook: optional callable(span, scope)
        response_hook: optional callable(span, scope, message)
        enable_view_spans: if True, adds a thin middleware that creates
                           view.<function> spans (mirrors Django ViewTracingMiddleware)
    """
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    except ImportError as e:
        raise ImportError(
            "opentelemetry-instrumentation-fastapi is required for FastAPI. "
            "Install with: pip install 'python-otel-sdk[fastapi]'"
        ) from e

    hooks = {}
    # FastAPIInstrumentor supports server_request_hook / client_request_hook
    # We wire our hooks that also call user hooks.
    def _req_hook(span, scope):
        _fastapi_request_hook(span, scope)
        if request_hook:
            try:
                request_hook(span, scope)
            except Exception:
                logger.debug("custom fastapi request_hook failed", exc_info=True)

    def _resp_hook(span, scope, message):
        _fastapi_response_hook(span, scope, message)
        if response_hook:
            try:
                response_hook(span, scope, message)
            except Exception:
                logger.debug("custom fastapi response_hook failed", exc_info=True)

    FastAPIInstrumentor.instrument_app(
        app,
        server_request_hook=_req_hook,
        client_request_hook=None,
    )

    # Also instrument the ASGI layer so http.route is captured correctly
    # FastAPIInstrumentor does this via instrument_app, but we keep response hook
    # by monkey-patching the ASGI app's send wrapper if needed.
    # Simpler: add middleware for response code enrichment if response_hook wasn't called.
    if enable_view_spans:
        _add_view_span_middleware(app)

    # Instrument common client libs used alongside FastAPI
    _instrument_common_libs()

    logger.info("FastAPI instrumentation enabled")


def _add_view_span_middleware(app):
    """Adds a Starlette middleware that creates view.<func> spans."""
    from starlette.middleware.base import BaseHTTPMiddleware

    class ViewSpanMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            # Resolve route name — FastAPI stores it in request.scope["route"]
            route = request.scope.get("route")
            endpoint = getattr(route, "endpoint", None) if route else None
            view_name = getattr(endpoint, "__name__", "unknown") if endpoint else "unknown"
            span_name = f"view.{view_name}.{request.method.lower()}"
            with _tracer.start_as_current_span(span_name) as span:
                span.set_attribute("view.name", view_name)
                if route and hasattr(route, "path_format"):
                    span.set_attribute("http.route", route.path_format)
                    span.set_attribute("url.name", getattr(route, "name", "") or "")
                response = await call_next(request)
                span.set_attribute("http.status_code", response.status_code)
                if response.status_code >= 400:
                    span.set_status(trace.Status(trace.StatusCode.ERROR))
                return response

    # Avoid double-registration
    if any(m.cls == ViewSpanMiddleware for m in app.user_middleware):
        return
    app.add_middleware(ViewSpanMiddleware)


def _instrument_common_libs():
    """Best-effort instrument libs typically used with FastAPI."""
    # SQLAlchemy / asyncpg / psycopg2
    for mod, cls_name in [
        ("opentelemetry.instrumentation.sqlalchemy", "SQLAlchemyInstrumentor"),
        ("opentelemetry.instrumentation.asyncpg", "AsyncPGInstrumentor"),
        ("opentelemetry.instrumentation.psycopg2", "Psycopg2Instrumentor"),
        ("opentelemetry.instrumentation.redis", "RedisInstrumentor"),
        ("opentelemetry.instrumentation.httpx", "HTTPXClientInstrumentor"),
        ("opentelemetry.instrumentation.requests", "RequestsInstrumentor"),
        ("opentelemetry.instrumentation.aiohttp_client", "AioHttpClientInstrumentor"),
    ]:
        try:
            mod_obj = __import__(mod, fromlist=[cls_name])
            getattr(mod_obj, cls_name)().instrument()
        except Exception:
            pass
