"""View span — wraps BaseHandler._get_response and resolves view name/route."""
from typing import Any, Callable

from opentelemetry.trace import SpanKind, StatusCode, get_tracer

from .request import _normalize_route, _resolve_view_name


def traced_get_response(wrapped: Callable, instance: Any, args: Any, kwargs: Any) -> Any:
    request = args[0] if args else kwargs.get("request")
    if request is None:
        return wrapped(*args, **kwargs)

    tracer = get_tracer("tp_obs_v3.django")
    initial_route = getattr(request, "path", "/")

    with tracer.start_as_current_span(
        "django.view",
        kind=SpanKind.INTERNAL,
        attributes={"django.view": "view", "http.route": initial_route},
    ) as span:
        try:
            response = wrapped(*args, **kwargs)
            method = getattr(request, "method", "GET")
            raw_view_name = _resolve_view_name(request, method)
            norm_route = _normalize_route(request, request.path if hasattr(request, "path") else initial_route)

            span.update_name(f"django.view.{raw_view_name}")
            span.set_attribute("django.view", raw_view_name)
            span.set_attribute("django.view.name", raw_view_name)
            span.set_attribute("http.route", norm_route)
            span.set_attribute("http.request.method", method.upper())
            span.set_status(StatusCode.OK)
            return response
        except Exception as exc:
            span.record_exception(exc)
            span.set_attribute("error", True)
            span.set_attribute("error.type", exc.__class__.__name__)
            span.set_status(StatusCode.ERROR, description=str(exc))
            raise
