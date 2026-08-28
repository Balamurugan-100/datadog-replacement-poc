"""
Redis/cache span processor — framework-agnostic.
"""
from opentelemetry.sdk.trace import SpanProcessor


class RedisSpanProcessor(SpanProcessor):
    """Enrich raw Redis command spans with normalized service metadata."""

    def on_start(self, span, parent_context=None):
        pass

    def on_end(self, span):
        attrs = dict(span.attributes or {})
        db_system = attrs.get("db.system")

        if db_system != "redis" or span.name.startswith("django_redis."):
            return

        redis_cmd = attrs.get("db.operation") or span.name
        peer = attrs.get("net.peer.name") or attrs.get("net.peer.ip") or "redis"
        span._name = f"{peer} {redis_cmd}"
        if span._attributes is not None:
            span._attributes["server.address"] = "redis"
            span._attributes["peer.service"] = "redis"
            span._attributes["server.port"] = 6379
            span._attributes["component"] = "redis"
            span._attributes["span.type"] = "cache"
            span._attributes["app.cache.operation"] = str(redis_cmd)
