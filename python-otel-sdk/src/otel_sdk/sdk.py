from __future__ import annotations
"""
Public SDK entry point — framework-agnostic init_tracing.

This is the single function users call regardless of framework.
It replaces django_otel_sdk.setup.init_tracing for all new code.

Examples:
    # Generic (auto-detect Django/FastAPI/Flask via OTEL_FRAMEWORKS or installed packages)
    from otel_sdk import init_tracing
    init_tracing()

    # Explicit with kwargs (env vars are fallback)
    init_tracing(service_name="my-service", environment="production")

    # FastAPI — pass the app so FastAPIInstrumentor can instrument_app
    from fastapi import FastAPI
    app = FastAPI()
    init_tracing(service_name="my-fastapi", frameworks=["fastapi"], app=app)

    # Django — still works via DjangoInstrumentor, no app needed
    init_tracing(frameworks=["django"])

    # Disable tracing
    init_tracing(enabled=False)
"""
import importlib.util
import logging
import os

from otel_sdk.core.config import OtelConfig
from otel_sdk.core.tracer import init_tracer_provider, shutdown_tracer_provider

logger = logging.getLogger("otel_sdk.sdk")

_initialized = False
_config: OtelConfig | None = None


def _is_enabled(config: OtelConfig) -> bool:
    return config.enabled


def _detect_frameworks() -> list[str]:
    """
    Auto-detect installed frameworks when OTEL_FRAMEWORKS env is not set.
    Order matters for logging.
    """
    detected = []
    mapping = {
        "django": "django",
        "fastapi": "fastapi",
        "flask": "flask",
        "starlette": "starlette",
    }
    for name, mod in mapping.items():
        if importlib.util.find_spec(mod) is not None:
            # Only auto-enable django/fastapi/flask if actually imported? We check spec exists.
            # For safety, only auto-enable django if DJANGO_SETTINGS_MODULE is set or django is importable
            # and user hasn't explicitly disabled via framework exclusion.
            # We default to including matched frameworks; user can restrict via OTEL_FRAMEWORKS env.
            detected.append(name)
    # If nothing detected, return [] — core tracer still initializes
    return detected


def _instrument_frameworks(frameworks: list[str], app=None, **instrument_kwargs):
    frameworks = [f.lower() for f in frameworks]

    if "django" in frameworks:
        try:
            from otel_sdk.frameworks.django import instrument_django

            instrument_django()
            logger.info("Django instrumentation enabled")
        except Exception as e:
            # Django may be installed but not configured (settings not ready) — downgrade to debug
            if "ImproperlyConfigured" in type(e).__name__ or "DJANGO_SETTINGS_MODULE" in str(e):
                logger.debug("Django instrumentation skipped (settings not configured): %s", e)
            else:
                logger.warning("Django instrumentation failed: %s", e, exc_info=True)

    if "fastapi" in frameworks:
        if app is None:
            logger.warning("FastAPI requested but no app= provided — skipping FastAPIInstrumentor.instrument_app. Call instrument_fastapi(app) manually after creating FastAPI().")
        else:
            try:
                from otel_sdk.frameworks.fastapi import instrument_fastapi

                instrument_fastapi(app, **instrument_kwargs.get("fastapi", {}))
            except Exception as e:
                logger.warning("FastAPI instrumentation failed: %s", e, exc_info=True)

    if "flask" in frameworks:
        if app is None:
            logger.warning("Flask requested but no app= provided — call instrument_flask(app) manually.")
        else:
            try:
                from otel_sdk.frameworks.flask import instrument_flask

                instrument_flask(app, **instrument_kwargs.get("flask", {}))
            except Exception as e:
                logger.warning("Flask instrumentation failed: %s", e, exc_info=True)

    if "starlette" in frameworks or "asgi" in frameworks:
        if app is not None:
            try:
                from otel_sdk.frameworks.starlette import instrument_starlette

                instrument_starlette(app)
            except Exception as e:
                logger.warning("Starlette/ASGI instrumentation failed: %s", e, exc_info=True)

    # Always try generic DB/cache/http libs — they are framework-agnostic
    _instrument_generic_libs(frameworks)


def _instrument_generic_libs(requested_frameworks: list[str]):
    """Instrument libs that are useful regardless of framework."""
    # psycopg2 / redis / requests are already covered by framework adapters,
    # but ensure they run even if only core is used.
    for mod, cls_name in [
        ("opentelemetry.instrumentation.psycopg2", "Psycopg2Instrumentor"),
        ("opentelemetry.instrumentation.redis", "RedisInstrumentor"),
        ("opentelemetry.instrumentation.requests", "RequestsInstrumentor"),
    ]:
        try:
            m = __import__(mod, fromlist=[cls_name])
            inst = getattr(m, cls_name)()
            # instrument() is idempotent in OTel — safe to call twice
            inst.instrument()
        except Exception:
            pass

    # SQL-related — only if sqlalchemy present
    try:
        import sqlalchemy  # noqa: F401
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().instrument()
    except Exception:
        pass

    # Celery
    try:
        import celery  # noqa: F401
        from opentelemetry.instrumentation.celery import CeleryInstrumentor

        CeleryInstrumentor().instrument()
    except Exception:
        pass


def init_tracing(
    service_name: str | None = None,
    environment: str | None = None,
    otlp_endpoint: str | None = None,
    enabled: bool | None = None,
    frameworks: list[str] | str | None = None,
    app=None,
    flush_timeout_ms: int | None = None,
    **kwargs,
) -> OtelConfig | None:
    """
    Initialize OpenTelemetry tracing.  Idempotent — safe to call multiple times.

    All args are optional and fall back to OTEL_* env vars.

    Args:
        service_name: OTEL_SERVICE_NAME (default: python-app)
        environment:  OTEL_ENVIRONMENT (default: development)
        otlp_endpoint: OTEL_EXPORTER_OTLP_ENDPOINT (default: http://localhost:4317)
        enabled: OTEL_ENABLED (default: true) — if False, no-ops
        frameworks: list of frameworks to instrument, e.g. ["django","fastapi"]
                    or comma-string "django,fastapi".  None = auto-detect.
                    Also respects OTEL_FRAMEWORKS env var.
        app: Framework app instance (FastAPI/Flask) — required for those frameworks
             so instrument_app(app) can be called.
        flush_timeout_ms: OTEL_FLUSH_TIMEOUT_MS
    Returns:
        OtelConfig that was used, or None if disabled.
    """
    global _initialized, _config
    if _initialized:
        logger.debug("OTel already initialized, skipping.")
        return _config

    # Build config from env + kwargs
    env_config = OtelConfig.from_env()

    # frameworks handling: explicit arg > env > auto-detect
    if frameworks is not None:
        if isinstance(frameworks, str):
            frameworks_list = [s.strip().lower() for s in frameworks.split(",") if s.strip()]
        else:
            frameworks_list = [s.lower() for s in frameworks]
    elif env_config.frameworks is not None:
        frameworks_list = list(env_config.frameworks)
    else:
        frameworks_list = _detect_frameworks()

    config = OtelConfig.from_kwargs(
        service_name=service_name,
        environment=environment,
        otlp_endpoint=otlp_endpoint,
        enabled=enabled if enabled is not None else env_config.enabled,
        flush_timeout_ms=flush_timeout_ms,
        frameworks=frameworks_list if frameworks_list else None,
    )
    # Normalize frameworks after from_kwargs (which may have taken env value)
    # We want the resolved list we computed
    # Rebuild config with resolved frameworks to avoid env/frameworks mismatch
    from dataclasses import replace

    config = replace(config, frameworks=tuple(frameworks_list) if frameworks_list else None)

    if not _is_enabled(config):
        logger.info("OTel tracing disabled (enabled=%s)", config.enabled)
        _initialized = True
        _config = config
        return config

    # Core tracer (must succeed before framework instrumentation)
    try:
        init_tracer_provider(config)
    except Exception as e:
        logger.error("Failed to init tracer provider: %s", e, exc_info=True)
        _initialized = True
        _config = config
        return config

    # Framework instrumentation — best effort
    _instrument_frameworks(frameworks_list, app=app, **kwargs)

    _initialized = True
    _config = config
    logger.info(
        "OTel SDK initialized: service=%s env=%s endpoint=%s frameworks=%s",
        config.service_name,
        config.environment,
        config.otlp_endpoint,
        frameworks_list or ["core"],
    )
    return config


def shutdown_tracing(timeout_ms: int | None = None):
    """Flush and shutdown the tracer.  Call on worker exit / app teardown."""
    global _initialized
    cfg_timeout = _config.flush_timeout_ms if _config else None
    shutdown_tracer_provider(timeout_ms or cfg_timeout)
    _initialized = False


def is_initialized() -> bool:
    return _initialized


def get_config() -> OtelConfig | None:
    return _config
