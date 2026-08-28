from __future__ import annotations
"""
Central OTEL configuration — single source of truth for all env vars.

All defaults match the previous django-otel-sdk so migration is zero-config.
"""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class OtelConfig:
    enabled: bool
    service_name: str
    environment: str
    application: str
    otlp_endpoint: str
    batch_delay_ms: int
    batch_max_size: int
    batch_queue_size: int
    flush_timeout_ms: int
    # optional — allow explicit framework selection
    frameworks: tuple[str, ...] | None = None

    @classmethod
    def from_env(cls) -> "OtelConfig":
        raw_enabled = os.getenv("OTEL_ENABLED", "true").lower()
        enabled = raw_enabled in ("true", "1", "yes")

        raw_frameworks = os.getenv("OTEL_FRAMEWORKS", "")
        frameworks = None
        if raw_frameworks.strip():
            frameworks = tuple(s.strip().lower() for s in raw_frameworks.split(",") if s.strip())

        return cls(
            enabled=enabled,
            service_name=os.getenv("OTEL_SERVICE_NAME", "python-app"),
            environment=os.getenv("OTEL_ENVIRONMENT", "development"),
            application=os.getenv("OTEL_APPLICATION", "python-otel"),
            otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
            batch_delay_ms=int(os.getenv("OTEL_BATCH_DELAY_MS", "2000")),
            batch_max_size=int(os.getenv("OTEL_BATCH_MAX_SIZE", "256")),
            batch_queue_size=int(os.getenv("OTEL_BATCH_QUEUE_SIZE", "2048")),
            flush_timeout_ms=int(os.getenv("OTEL_FLUSH_TIMEOUT_MS", "5000")),
            frameworks=frameworks,
        )

    @classmethod
    def from_kwargs(cls, **kwargs) -> "OtelConfig":
        env = cls.from_env()

        def _pick(key, env_val):
            v = kwargs.get(key, None)
            return v if v is not None else env_val

        # app_name alias for application
        app_val = kwargs.get("application", kwargs.get("app_name", None))
        if app_val is None:
            app_val = env.application

        fw = kwargs.get("frameworks", None)
        if fw is not None:
            fw_tuple = tuple(fw) if not isinstance(fw, tuple) else fw
        else:
            fw_tuple = env.frameworks

        return cls(
            enabled=_pick("enabled", env.enabled),
            service_name=_pick("service_name", env.service_name),
            environment=_pick("environment", env.environment),
            application=app_val,
            otlp_endpoint=_pick("otlp_endpoint", _pick("endpoint", env.otlp_endpoint)),
            batch_delay_ms=_pick("batch_delay_ms", env.batch_delay_ms),
            batch_max_size=_pick("batch_max_size", env.batch_max_size),
            batch_queue_size=_pick("batch_queue_size", env.batch_queue_size),
            flush_timeout_ms=_pick("flush_timeout_ms", env.flush_timeout_ms),
            frameworks=fw_tuple,
        )
