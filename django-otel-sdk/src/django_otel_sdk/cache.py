"""
Shim — re-exports RedisSpanProcessor from otel_sdk.processors.cache.
"""
try:
    from otel_sdk.processors.cache import RedisSpanProcessor  # noqa: F401
    from otel_sdk.frameworks.django import instrument_django_redis  # noqa: F401
except ImportError:
    from functools import wraps
    from opentelemetry import trace
    from opentelemetry.sdk.trace import SpanProcessor
    from django_otel_sdk.utils.context import extract_cache_resource_namespace

    _tracer = trace.get_tracer("django_redis.cache")

    class RedisSpanProcessor(SpanProcessor):  # type: ignore[no-redef]
        def on_end(self, span):
            attrs = dict(span.attributes or {})
            if attrs.get("db.system") != "redis" or span.name.startswith("django_redis."):
                return
            cmd = attrs.get("db.operation") or span.name
            peer = attrs.get("net.peer.name") or attrs.get("net.peer.ip") or "redis"
            span._name = f"{peer} {cmd}"
            if span._attributes is not None:
                span._attributes["server.address"] = "redis"
                span._attributes["peer.service"] = "redis"
                span._attributes["server.port"] = 6379
                span._attributes["component"] = "redis"
                span._attributes["span.type"] = "cache"
                span._attributes["app.cache.operation"] = str(cmd)

    def instrument_django_redis():  # type: ignore[no-redef]
        try:
            from django_redis.client import DefaultClient
        except ImportError:
            return
        for target_method_name in ["get", "set", "delete", "get_many", "set_many", "incr", "decr", "touch", "clear"]:
            if not hasattr(DefaultClient, target_method_name):
                continue
            orig = getattr(DefaultClient, target_method_name)
            if getattr(orig, "_is_otel_traced", False):
                continue

            def build(method_name, orig_method):
                @wraps(orig_method)
                def wrapper(self, *args, **kwargs):
                    with _tracer.start_as_current_span(f"django_redis.cache.{method_name}", kind=trace.SpanKind.CLIENT) as s:
                        s.set_attribute("db.system", "redis")
                        s.set_attribute("server.address", "redis")
                        s.set_attribute("peer.service", "redis")
                        s.set_attribute("component", "redis")
                        s.set_attribute("span.type", "cache")
                        s.set_attribute("app.cache.operation", method_name.upper())
                        if args:
                            k = str(args[0])
                            s.set_attribute("cache.key", k)
                            s.set_attribute("app.cache.resource", extract_cache_resource_namespace(k))
                            s.set_attribute("db.statement", f"{method_name.upper()} {k}")
                        return orig_method(self, *args, **kwargs)

                wrapper._is_otel_traced = True
                return wrapper

            setattr(DefaultClient, target_method_name, build(target_method_name, orig))
