"""Request (SERVER) span + metrics. Owns _normalize_route / _resolve_view_name."""
import time
import logging
from typing import Any, Dict
from urllib.parse import urlparse

from opentelemetry.propagate import extract
from opentelemetry.trace import SpanKind, StatusCode, get_tracer

from tp_obs_v3.sanitize import sanitize_url
from .metrics import get_metrics

logger = logging.getLogger("tp_obs_v3.integrations.django.request")


def _format_trace_id(trace_id: int) -> str:
    return format(trace_id, "032x")


def _format_span_id(span_id: int) -> str:
    return format(span_id, "016x")


def _normalize_route(request, fallback_path: str) -> str:
    """Low-cardinality http.route from resolver_match, never raw URL with IDs."""
    resolver_match = getattr(request, "resolver_match", None)
    route = None
    if resolver_match:
        if getattr(resolver_match, "route", None):
            route = resolver_match.route
        elif hasattr(resolver_match, "_urlpattern") and hasattr(resolver_match._urlpattern, "pattern"):
            try:
                pat = resolver_match._urlpattern.pattern  # type: ignore
                route = pat.regex.pattern if hasattr(pat, "regex") else str(pat)
            except Exception:
                pass
        elif getattr(resolver_match, "url_name", None):
            route = resolver_match.url_name
    if not route:
        route = fallback_path
    route_str = str(route)
    if route_str and not route_str.startswith("/"):
        route_str = f"/{route_str}"
    return route_str


def _resolve_view_name(request, method: str) -> str:
    resolver_match = getattr(request, "resolver_match", None)
    if not resolver_match:
        return "view"
    view_func = getattr(resolver_match, "func", None)
    cls = None
    if view_func is not None:
        cls = getattr(view_func, "cls", getattr(view_func, "view_class", None))
    actions = getattr(view_func, "actions", {}) if view_func else {}
    req_method = method.lower()
    if cls and isinstance(actions, dict) and req_method in actions:
        return f"{cls.__name__}.{actions[req_method]}"
    elif cls:
        return cls.__name__
    elif hasattr(resolver_match, "_func_path"):
        return str(resolver_match._func_path).split(".")[-1]
    elif view_func and hasattr(view_func, "__name__"):
        return str(view_func.__name__)
    elif getattr(resolver_match, "view_name", None):
        return str(resolver_match.view_name)
    return "view"


def traced_get_response(wrapped, instance, args, kwargs):
    """Wrapper for BaseHandler.get_response — SERVER span + metrics."""
    request = args[0] if args else kwargs.get("request")
    if request is None:
        return wrapped(*args, **kwargs)

    # Re-entrancy guard: same request object can be re-entered if middleware
    # calls get_response recursively. We store state on the request object
    # itself (per-request, not global) — minimal, cleared per-request.
    # Alternative would be context var check, but this is simpler and avoids
    # creating nested SERVER spans for the same logical request.
    if getattr(request, "_tp_traced_request", False):
        return wrapped(*args, **kwargs)
    request._tp_traced_request = True

    tracer = get_tracer("tp_obs_v3.django")
    method = getattr(request, "method", "GET").upper()
    path = getattr(request, "path", "/")
    scheme = getattr(request, "scheme", "http")

    # W3C propagation
    carrier: Dict[str, str] = {}
    if hasattr(request, "headers"):
        try:
            carrier.update({k.lower(): str(v) for k, v in request.headers.items()})
        except Exception:
            pass
    elif hasattr(request, "META") and isinstance(request.META, dict):
        for k, v in request.META.items():
            if k.startswith("HTTP_"):
                carrier[k[5:].replace("_", "-").lower()] = str(v)
            elif k in ("CONTENT_TYPE", "CONTENT_LENGTH"):
                carrier[k.replace("_", "-").lower()] = str(v)

    parent_ctx = extract(carrier)

    try:
        raw_url = request.build_absolute_uri() if hasattr(request, "build_absolute_uri") else path
    except Exception:
        raw_url = path
    sanitized = sanitize_url(raw_url)
    url_path = path
    url_query = ""
    try:
        parsed = urlparse(sanitized)
        url_path = parsed.path or path
        url_query = parsed.query
    except Exception:
        pass

    # Stable semconv only — old `http.method` / `http.target` etc. removed.
    # Migration note: if you still query old keys, update to `http.request.method` / `url.path` / `http.route`.
    span_attrs: Dict[str, Any] = {
        "http.request.method": method,
        "url.full": sanitized,
        "url.path": url_path,
        "url.scheme": scheme,
        "user_agent.original": carrier.get("user-agent", ""),
    }
    if url_query:
        span_attrs["url.query"] = url_query
    if hasattr(request, "META") and isinstance(request.META, dict):
        remote_ip = request.META.get("REMOTE_ADDR")
        if remote_ip:
            span_attrs["client.address"] = str(remote_ip)
        remote_port = request.META.get("REMOTE_PORT")
        if remote_port:
            try:
                span_attrs["client.port"] = int(remote_port)
            except Exception:
                pass
        host = request.META.get("HTTP_HOST") or request.META.get("SERVER_NAME")
        if host:
            span_attrs["server.address"] = str(host).split(":")[0]

    start = time.monotonic()

    with tracer.start_as_current_span(
        "django.request",
        context=parent_ctx,
        kind=SpanKind.SERVER,
        attributes=span_attrs,
    ) as span:
        span_ctx = span.get_span_context()
        trace_id_hex = _format_trace_id(span_ctx.trace_id)
        span_id_hex = _format_span_id(span_ctx.span_id)
        request._tp_span = span
        request.trace_id = trace_id_hex
        request.span_id = span_id_hex

        status_code = 200
        error = False
        route_for_metrics = path
        try:
            response = wrapped(*args, **kwargs)
            status_code = getattr(response, "status_code", 200)
            norm_route = _normalize_route(request, path)
            route_for_metrics = norm_route
            view_name = _resolve_view_name(request, method)

            span.set_attribute("http.route", norm_route)
            span.set_attribute("http.response.status_code", status_code)
            span.set_attribute("django.view.name", str(view_name))

            if status_code >= 500:
                error = True
                span.set_attribute("error", True)
                span.set_attribute("error.type", str(status_code))
                span.set_status(StatusCode.ERROR, description=f"HTTP {status_code}")
            elif status_code >= 400:
                span.set_attribute("error", False)
                span.set_status(StatusCode.OK)
            else:
                span.set_attribute("error", False)
                span.set_status(StatusCode.OK)

            if hasattr(response, "headers") or hasattr(response, "__setitem__"):
                try:
                    response["X-Trace-ID"] = trace_id_hex
                    response["X-Span-ID"] = span_id_hex
                except Exception:
                    pass

            return response
        except Exception as exc:
            error = True
            status_code = 500
            try:
                route_for_metrics = _normalize_route(request, path)
                span.set_attribute("http.route", route_for_metrics)
            except Exception:
                pass
            span.record_exception(exc)
            span.set_attribute("error", True)
            span.set_attribute("error.type", exc.__class__.__name__)
            span.set_attribute("http.response.status_code", 500)
            span.set_status(StatusCode.ERROR, description=str(exc))
            raise
        finally:
            duration = time.monotonic() - start
            try:
                _, req_counter, err_counter, dur_hist = get_metrics()
                metric_attrs = {
                    "http.request.method": method,
                    "http.route": route_for_metrics,
                    "http.response.status_code": int(status_code),
                }
                if req_counter is not None:
                    req_counter.add(1, attributes=metric_attrs)
                if dur_hist is not None:
                    dur_hist.record(duration, attributes=metric_attrs)
                if error and err_counter is not None:
                    err_counter.add(1, attributes=metric_attrs)
            except Exception as exc:
                logger.debug("Failed to record Django metrics: %s", exc)
