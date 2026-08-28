"""
Django instrumentation adapter — moved from django_otel_sdk/django.py.

All logic is identical; import path is now otel_sdk.frameworks.django
with a shim left at django_otel_sdk.django for backward compat.
"""
import logging

from opentelemetry import context as otel_context
from opentelemetry import trace

logger = logging.getLogger("otel_sdk.frameworks.django")

_tracer = trace.get_tracer("django.middleware")
_template_tracer = trace.get_tracer("django.template")


def django_response_hook(span, request, response):
    """Enrich the root HTTP span with route pattern, method, and status code."""
    if not span.is_recording():
        return

    url_resolver_match = getattr(request, "resolver_match", None)
    http_method_name = getattr(request, "method", "GET")

    formatted_route_pattern = None
    if url_resolver_match and getattr(url_resolver_match, "route", None):
        route_pattern = url_resolver_match.route
        formatted_route_pattern = f"^{route_pattern}" if not route_pattern.startswith("^") else route_pattern
        span.update_name(f"{http_method_name} {formatted_route_pattern}")
        span.set_attribute("http.route", formatted_route_pattern)
    else:
        request_path = getattr(request, "path", "/")
        span.update_name(f"{http_method_name} {request_path}")

    span.set_attribute("http.method", http_method_name)
    span.set_attribute("http.status_code", response.status_code)
    if response.status_code >= 400:
        span.set_status(trace.Status(trace.StatusCode.ERROR, f"HTTP {response.status_code}"))

    if hasattr(request, "META") and "otel.root_span" in request.META:
        root_span = request.META["otel.root_span"]
        if root_span and root_span.is_recording():
            if formatted_route_pattern:
                root_span.update_name(f"{http_method_name} {formatted_route_pattern}")
                root_span.set_attribute("http.route", formatted_route_pattern)
            else:
                request_path = getattr(request, "path", "/")
                root_span.update_name(f"{http_method_name} {request_path}")

            root_span.set_attribute("http.method", http_method_name)
            root_span.set_attribute("http.status_code", response.status_code)
            if response.status_code >= 400:
                root_span.set_status(trace.Status(trace.StatusCode.ERROR, f"HTTP {response.status_code}"))

            request_correlation_id = getattr(request, "_request_id", None)
            if request_correlation_id:
                root_span.set_attribute("request.id", request_correlation_id)


def instrument_middleware():
    """Wrap Django middleware loading to create individual spans per middleware."""
    try:
        from django.core.handlers import base
    except ImportError:
        return

    if getattr(base.BaseHandler.load_middleware, "_is_otel_traced", False):
        return

    original_load_middleware = base.BaseHandler.load_middleware

    def load_middleware_traced(self, *args, **kwargs):
        original_import_string = base.import_string

        def import_string_traced(middleware_class_path):
            middleware_class = original_import_string(middleware_class_path)

            if middleware_class_path in (
                "django_otel_sdk.django.ViewTracingMiddleware",
                "django_otel_sdk.ViewTracingMiddleware",
                "otel_sdk.frameworks.django.ViewTracingMiddleware",
                "otel_sdk.ViewTracingMiddleware",
            ):
                return middleware_class

            def middleware_factory(get_response):
                middleware_instance = middleware_class(get_response)
                middleware_span_name = f"{middleware_class.__module__}.{middleware_class.__qualname__}"

                def traced_middleware(request):
                    with _tracer.start_as_current_span(middleware_span_name) as span:
                        try:
                            response = middleware_instance(request)
                            if hasattr(response, "status_code"):
                                span.set_attribute("http.status_code", response.status_code)
                            return response
                        except Exception as exception_caught:
                            span.set_status(trace.Status(trace.StatusCode.ERROR, str(exception_caught)))
                            span.record_exception(exception_caught)
                            raise

                for capability_attribute in ("sync_capable", "async_capable"):
                    if hasattr(middleware_class, capability_attribute):
                        setattr(traced_middleware, capability_attribute, getattr(middleware_class, capability_attribute))

                for hook_method_name in ("process_view", "process_exception", "process_template_response"):
                    if hasattr(middleware_instance, hook_method_name):
                        setattr(traced_middleware, hook_method_name, getattr(middleware_instance, hook_method_name))

                return traced_middleware

            return middleware_factory

        base.import_string = import_string_traced
        try:
            return original_load_middleware(self, *args, **kwargs)
        finally:
            base.import_string = original_import_string

    load_middleware_traced._is_otel_traced = True
    base.BaseHandler.load_middleware = load_middleware_traced


def instrument_template_render():
    """Wrap Django template rendering to create spans per template."""
    try:
        from django.template.base import Template

        if getattr(Template.render, "_is_otel_traced", False):
            return

        original_render_method = Template.render

        def render_traced(self, context):
            template_name = getattr(self, "name", None) or "string_template"
            template_span_name = f"django.template.render {template_name}"
            with _template_tracer.start_as_current_span(template_span_name) as span:
                span.set_attribute("template.name", str(template_name))
                span.set_attribute("component", "django")
                return original_render_method(self, context)

        render_traced._is_otel_traced = True
        Template.render = render_traced
    except Exception:
        logger.debug("Failed to instrument template rendering", exc_info=True)


def instrument_django_db_aliases():
    """Hook Django's cursor creation to track which database alias is being used."""
    try:
        from django.db.backends.base.base import BaseDatabaseWrapper

        from otel_sdk.utils.context import get_service_name_for_connection, set_active_db_context

        if getattr(BaseDatabaseWrapper._cursor, "_is_otel_traced", False):
            return

        original_cursor_method = BaseDatabaseWrapper._cursor

        def cursor_traced(self, *args, **kwargs):
            connection_target_service = get_service_name_for_connection(self)
            set_active_db_context(connection_target_service)
            return original_cursor_method(self, *args, **kwargs)

        cursor_traced._is_otel_traced = True
        BaseDatabaseWrapper._cursor = cursor_traced
    except Exception:
        pass


def instrument_psycopg2_rowcount():
    """Hook psycopg2 execute/executemany to capture db.row_count on the active span."""
    try:
        import psycopg2.extensions

        if getattr(psycopg2.extensions.cursor.execute, "_is_otel_traced", False):
            return

        original_execute_method = psycopg2.extensions.cursor.execute
        original_executemany_method = psycopg2.extensions.cursor.executemany

        def execute_traced(self, query, vars=None):
            execution_result = original_execute_method(self, query, vars)
            current_active_span = trace.get_current_span()
            if current_active_span and current_active_span.is_recording():
                affected_row_count = getattr(self, "rowcount", -1)
                current_active_span.set_attribute(
                    "db.row_count", affected_row_count if (affected_row_count is not None and affected_row_count >= 0) else -1
                )
            return execution_result

        def executemany_traced(self, query, vars_list):
            execution_result = original_executemany_method(self, query, vars_list)
            current_active_span = trace.get_current_span()
            if current_active_span and current_active_span.is_recording():
                affected_row_count = getattr(self, "rowcount", -1)
                current_active_span.set_attribute(
                    "db.row_count", affected_row_count if (affected_row_count is not None and affected_row_count >= 0) else -1
                )
            return execution_result

        execute_traced._is_otel_traced = True
        executemany_traced._is_otel_traced = True

        psycopg2.extensions.cursor.execute = execute_traced
        psycopg2.extensions.cursor.executemany = executemany_traced
    except Exception:
        pass


def instrument_django_redis():
    """Wrap django_redis DefaultClient methods with explicit OTel spans."""
    try:
        from django_redis.client import DefaultClient
    except (ImportError, Exception):
        return

    from functools import wraps

    from otel_sdk.utils.context import extract_cache_resource_namespace

    _redis_tracer = trace.get_tracer("django_redis.cache")

    redis_cache_methods = [
        "get",
        "set",
        "delete",
        "get_many",
        "set_many",
        "incr",
        "decr",
        "touch",
        "clear",
    ]

    for target_method_name in redis_cache_methods:
        if not hasattr(DefaultClient, target_method_name):
            continue
        original_client_method = getattr(DefaultClient, target_method_name)
        if getattr(original_client_method, "_is_otel_traced", False):
            continue

        def build_client_method_wrapper(method_name, original_method):
            @wraps(original_method)
            def client_method_wrapper(self, *args, **kwargs):
                cache_span_name = f"django_redis.cache.{method_name}"
                with _redis_tracer.start_as_current_span(cache_span_name, kind=trace.SpanKind.CLIENT) as span:
                    span.set_attribute("db.system", "redis")
                    span.set_attribute("server.address", "redis")
                    span.set_attribute("peer.service", "redis")
                    span.set_attribute("component", "redis")
                    span.set_attribute("span.type", "cache")
                    span.set_attribute("app.cache.operation", method_name.upper())

                    if args:
                        cache_key_string = str(args[0])
                        cache_resource_namespace = extract_cache_resource_namespace(cache_key_string)
                        span.set_attribute("cache.key", cache_key_string)
                        span.set_attribute("app.cache.resource", cache_resource_namespace)
                        span.set_attribute("db.statement", f"{method_name.upper()} {cache_key_string}")
                    return original_method(self, *args, **kwargs)

            client_method_wrapper._is_otel_traced = True
            return client_method_wrapper

        setattr(DefaultClient, target_method_name, build_client_method_wrapper(target_method_name, original_client_method))


class ViewTracingMiddleware:
    """
    Creates view-level spans using Django's process_view hook.
    Usage: Add 'otel_sdk.frameworks.django.ViewTracingMiddleware' to MIDDLEWARE
    (old path 'django_otel_sdk.django.ViewTracingMiddleware' still works via shim).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        active_view_span = getattr(request, "_otel_view_span", None)
        active_view_token = getattr(request, "_otel_view_token", None)
        if active_view_span:
            active_view_span.set_attribute("http.status_code", response.status_code)
            if response.status_code >= 400:
                active_view_span.set_status(trace.Status(trace.StatusCode.ERROR))
            active_view_span.end()
        if active_view_token:
            otel_context.detach(active_view_token)

        return response

    def process_view(self, request, view_func, view_args, view_kwargs):
        url_resolver_match = getattr(request, "resolver_match", None)

        view_class_instance = getattr(view_func, "cls", None) or getattr(view_func, "view_class", None)
        view_class_name = (
            view_class_instance.__name__
            if view_class_instance
            else getattr(view_func, "__name__", "unknown")
        )

        view_action_name = None
        if hasattr(view_func, "actions") and isinstance(view_func.actions, dict):
            view_action_name = view_func.actions.get(request.method.lower())

        if view_action_name:
            view_span_name = f"view.{view_class_name}.{view_action_name}"
        else:
            http_method_name = request.method.lower()
            view_span_name = f"view.{view_class_name}.{http_method_name}"

        view_span = _tracer.start_span(view_span_name)
        view_span.set_attribute("view.name", view_class_name)
        if view_action_name:
            view_span.set_attribute("view.action", view_action_name)
        if url_resolver_match:
            view_span.set_attribute("url.name", url_resolver_match.url_name or "")

        span_activation_token = otel_context.attach(trace.set_span_in_context(view_span))
        request._otel_view_span = view_span
        request._otel_view_token = span_activation_token

        return None


def instrument_django():
    """Convenience: enable all Django instrumentation in one call."""
    instrument_django_db_aliases()
    instrument_psycopg2_rowcount()
    instrument_django_redis()
    instrument_middleware()
    instrument_template_render()
    try:
        from opentelemetry.instrumentation.django import DjangoInstrumentor
        from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        DjangoInstrumentor().instrument(
            response_hook=django_response_hook,
            is_middleware_instrumentation_enabled=True,
        )
        Psycopg2Instrumentor().instrument()
        RedisInstrumentor().instrument()
        RequestsInstrumentor().instrument()
    except Exception:
        logger.debug("Django instrumentors failed", exc_info=True)
