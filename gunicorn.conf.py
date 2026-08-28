import multiprocessing
import os

bind = "0.0.0.0:8000"
workers = int(os.environ.get("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
timeout = 120
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = "info"


def post_fork(server, worker):
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    """Initialize OpenTelemetry SDK after worker forks — gives each worker its own tracer."""
    try:
        from django_otel_sdk.setup import init_tracing
        init_tracing()
    except Exception as e:
        server.log.error(f"OTel post_fork init failed: {e}")


def worker_exit(server, worker):
    """Flush in-flight trace batches before worker terminates."""
    try:
        from opentelemetry import trace
        provider = trace.get_tracer_provider()
        flush_timeout = int(os.environ.get("OTEL_FLUSH_TIMEOUT_MS", "5000"))
        if hasattr(provider, "force_flush"):
            provider.force_flush(timeout_millis=flush_timeout)
    except Exception:
        pass
