"""Middleware spans — wraps each class-based middleware's __call__.

Sync-only for this PoC. Async (ASGI) would need `await` + separate span
lifecycle; test with ASGIHandler before enabling.
"""
import logging
from typing import Any, Callable

from opentelemetry.trace import SpanKind, StatusCode, get_tracer

logger = logging.getLogger("tp_obs_v3.integrations.django.middleware")


def make_traced_load_middleware(integration):
    """Factory that captures the DjangoIntegration instance so we can use
    its instance set `integration._wrapped_middleware` (not global)."""

    def traced_load_middleware(wrapped: Callable, instance: Any, args: Any, kwargs: Any):
        if getattr(instance, "_tp_middleware_instrumented", False):
            return wrapped(*args, **kwargs)

        result = wrapped(*args, **kwargs)

        try:
            from django.conf import settings
            from django.utils.module_loading import import_string

            for middleware_path in settings.MIDDLEWARE:
                if middleware_path in integration._wrapped_middleware:
                    continue
                try:
                    middleware = import_string(middleware_path)
                    if not isinstance(middleware, type):
                        logger.debug("Skipping function-based middleware %s (PoC: class-based only)", middleware_path)
                        integration._wrapped_middleware.add(middleware_path)
                        continue
                    middleware_short = middleware_path.rsplit(".", 1)[-1]

                    def _make_wrapper(mw_path: str, mw_short: str):
                        def _wrapper(wrapped_call, inst, a, k):
                            request = a[0] if a else k.get("request")
                            if request is None:
                                return wrapped_call(*a, **k)
                            tracer = get_tracer("tp_obs_v3.django")
                            span_name = f"django.middleware.{mw_short}"
                            attrs = {"django.middleware": mw_path, "django.middleware.name": mw_short}
                            with tracer.start_as_current_span(span_name, kind=SpanKind.INTERNAL, attributes=attrs) as span:
                                try:
                                    resp = wrapped_call(*a, **k)
                                    span.set_status(StatusCode.OK)
                                    return resp
                                except Exception as exc:
                                    span.record_exception(exc)
                                    span.set_status(StatusCode.ERROR, str(exc))
                                    raise

                        return _wrapper

                    integration.wrap(middleware, "__call__", _make_wrapper(middleware_path, middleware_short))
                    integration._wrapped_middleware.add(middleware_path)
                except Exception as exc:
                    logger.debug("Failed to instrument middleware %s: %s", middleware_path, exc)
        except Exception as exc:
            logger.debug("Middleware instrumentation post-load failed: %s", exc)

        instance._tp_middleware_instrumented = True
        return result

    return traced_load_middleware
