# Shim — delegates to the new framework-agnostic SDK (python-otel-sdk).
# Keeps `import django_otel_sdk` working for existing code.
try:
    from otel_sdk.frameworks.django import ViewTracingMiddleware  # noqa: F401
    from otel_sdk import shutdown_tracing  # noqa: F401
    from .setup import init_tracing  # shim with django default service_name  # noqa: F401

    __all__ = ["init_tracing", "shutdown_tracing", "ViewTracingMiddleware"]
except ImportError:
    # Fallback when python-otel-sdk not installed (standalone legacy install)
    from .django import ViewTracingMiddleware  # noqa: F401
    from .setup import init_tracing  # noqa: F401

    __all__ = ["init_tracing", "ViewTracingMiddleware"]
