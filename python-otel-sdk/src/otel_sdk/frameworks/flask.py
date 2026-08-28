"""
Flask instrumentation adapter.
"""
import logging

from opentelemetry import trace

logger = logging.getLogger("otel_sdk.frameworks.flask")
_tracer = trace.get_tracer("flask.middleware")


def instrument_flask(app, request_hook=None, response_hook=None):
    try:
        from opentelemetry.instrumentation.flask import FlaskInstrumentor
    except ImportError as e:
        raise ImportError(
            "opentelemetry-instrumentation-flask required. pip install 'python-otel-sdk[flask]'"
        ) from e

    def _req_hook(span, environ):
        if span and span.is_recording() and request_hook:
            try:
                request_hook(span, environ)
            except Exception:
                logger.debug("flask request_hook failed", exc_info=True)

    def _resp_hook(span, status, headers):
        if span and span.is_recording():
            try:
                code = int(str(status).split()[0])
                span.set_attribute("http.status_code", code)
                if code >= 400:
                    span.set_status(trace.Status(trace.StatusCode.ERROR, f"HTTP {code}"))
            except Exception:
                pass
            if response_hook:
                try:
                    response_hook(span, status, headers)
                except Exception:
                    logger.debug("flask response_hook failed", exc_info=True)

    FlaskInstrumentor().instrument_app(
        app,
        request_hook=_req_hook if request_hook else None,
        response_hook=_resp_hook,
    )

    # instrument common libs
    for mod, cls_name in [
        ("opentelemetry.instrumentation.sqlalchemy", "SQLAlchemyInstrumentor"),
        ("opentelemetry.instrumentation.psycopg2", "Psycopg2Instrumentor"),
        ("opentelemetry.instrumentation.redis", "RedisInstrumentor"),
        ("opentelemetry.instrumentation.requests", "RequestsInstrumentor"),
    ]:
        try:
            m = __import__(mod, fromlist=[cls_name])
            getattr(m, cls_name)().instrument()
        except Exception:
            pass

    logger.info("Flask instrumentation enabled")
