"""
Django example — new SDK is drop-in replacement; old import still works.

settings.py:
    MIDDLEWARE = [
        ...,
        "otel_sdk.frameworks.django.ViewTracingMiddleware",
        # old path still works: "django_otel_sdk.django.ViewTracingMiddleware"
    ]

gunicorn.conf.py:
    def post_fork(server, worker):
        from otel_sdk import init_tracing
        init_tracing(service_name="django", frameworks=["django"])

    def worker_exit(server, worker):
        from otel_sdk import shutdown_tracing
        shutdown_tracing()

Manual spans in views:
    from otel_sdk import traced, span
    @traced
    def my_view(request): ...
"""
# Minimal runnable demo without Django installed — just shows API
from otel_sdk import init_tracing, traced, span

# Core init (auto-detects Django if installed)
init_tracing(service_name="django-demo", frameworks=["django"])

@traced(span_name="demo.business_logic")
def business_logic(x: int) -> int:
    with span("demo.compute", attributes={"input": x}):
        return x * 2

if __name__ == "__main__":
    print(business_logic(21))
    print("Run with OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 to export")
