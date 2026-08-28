from __future__ import annotations
"""
Manual instrumentation helpers — framework-agnostic.

Usage:
    from otel_sdk import traced, span, add_span_attributes

    @traced("order.process")
    def process_order(order_id): ...

    @traced(span_name="custom", attributes={"order.id": 123})
    async def async_work(): ...

    with span("db.seed"):
        seed_db()

    add_span_attributes({"user.id": 42})
"""
import functools
import inspect
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

_tracer = trace.get_tracer("otel_sdk.manual")


def traced(_func=None, *, span_name: str | None = None, kind: SpanKind = SpanKind.INTERNAL, attributes: dict | None = None):
    """
    Decorator that wraps a function (sync or async) in a span.
    If used without args: @traced  — span name defaults to module.qualname
    """
    def decorator(func):
        name = span_name or f"{func.__module__}.{func.__qualname__}"

        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                with _tracer.start_as_current_span(name, kind=kind) as s:
                    if attributes:
                        for k, v in attributes.items():
                            s.set_attribute(k, v)
                    try:
                        result = await func(*args, **kwargs)
                        return result
                    except Exception as e:
                        s.set_status(Status(StatusCode.ERROR, str(e)))
                        s.record_exception(e)
                        raise
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                with _tracer.start_as_current_span(name, kind=kind) as s:
                    if attributes:
                        for k, v in attributes.items():
                            s.set_attribute(k, v)
                    try:
                        result = func(*args, **kwargs)
                        return result
                    except Exception as e:
                        s.set_status(Status(StatusCode.ERROR, str(e)))
                        s.record_exception(e)
                        raise
            return sync_wrapper

    if _func is not None:
        return decorator(_func)
    return decorator


@contextmanager
def span(name: str, kind: SpanKind = SpanKind.INTERNAL, attributes: dict | None = None):
    """Context manager for manual spans."""
    with _tracer.start_as_current_span(name, kind=kind) as s:
        if attributes:
            for k, v in attributes.items():
                s.set_attribute(k, v)
        try:
            yield s
        except Exception as e:
            s.set_status(Status(StatusCode.ERROR, str(e)))
            s.record_exception(e)
            raise


def add_span_attributes(attributes: dict):
    """Add attributes to the current active span (no-op if no span)."""
    cur = trace.get_current_span()
    if cur and cur.is_recording():
        for k, v in attributes.items():
            cur.set_attribute(k, v)


def set_span_error(message: str):
    cur = trace.get_current_span()
    if cur and cur.is_recording():
        cur.set_status(Status(StatusCode.ERROR, message))


# Alias for Datadog-style naming
trace_method = traced
trace_function = traced
