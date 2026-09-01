"""
tp_obs_v3: Python Observability SDK for OpenTelemetry.

Provides complete waterfall tracing, auto-instrumentation, and drop-in ddtrace replacement.
"""

import atexit
import logging
import threading
from typing import Any, Dict, List, Optional, Union

from opentelemetry import trace, metrics
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader, MetricExporter, MetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource

# Explicit bucket boundaries for http.server.request.duration (seconds)
# Configured via View, not on instrument — see MeterProvider(views=...)
_HTTP_DURATION_BOUNDARIES = [0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0]
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.sdk.trace import SpanProcessor
from opentelemetry.sdk.trace.sampling import (
    ALWAYS_OFF,
    ALWAYS_ON,
    ParentBased,
    TraceIdRatioBased,
)
from opentelemetry.trace import get_current_span, get_tracer
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.propagate import set_global_textmap

from tp_obs_v3.config import SDKConfig
from tp_obs_v3.integrations import BaseIntegration, get_integration_manager
from tp_obs_v3.sanitize import sanitize_sql, sanitize_url
from tp_obs_v3.version import __version__

logger = logging.getLogger("tp_obs_v3")

_INITIALIZED = False
_INIT_LOCK = threading.Lock()
_ACTIVE_PROVIDER: Optional[TracerProvider] = None
_ACTIVE_METER_PROVIDER: Optional[MeterProvider] = None
_ACTIVE_CONFIG: Optional[SDKConfig] = None


def init(
    service: Optional[str] = None,
    service_name: Optional[str] = None,
    environment: Optional[str] = None,
    version: Optional[str] = None,
    endpoint: Optional[str] = None,
    traces_endpoint: Optional[str] = None,
    metrics_endpoint: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    sample_rate: Optional[float] = None,
    disabled: Optional[bool] = None,
    debug: Optional[bool] = None,
    resource_attributes: Optional[Dict[str, Any]] = None,
    integrations: Optional[Dict[str, bool]] = None,
    exporter: Optional[SpanExporter] = None,
    span_processor: Optional[SpanProcessor] = None,
    metric_exporter: Optional[MetricExporter] = None,
    metric_reader: Optional[MetricReader] = None,
    export_batch: bool = True,
    **kwargs: Any,
) -> TracerProvider:
    """
    Initialize the tp_obs_v3 OpenTelemetry SDK.

    Configures the global TracerProvider + MeterProvider, W3C context propagation,
    and OTLP HTTP exporters. Thread-safe and idempotent.

    Resource attributes follow OTel semantic conventions:
      service.name, service.version, deployment.environment.name, service.instance.id
    The integration itself obtains tracers/meters from the global providers
    rather than constructing providers internally.
    """
    global _INITIALIZED, _ACTIVE_PROVIDER, _ACTIVE_METER_PROVIDER, _ACTIVE_CONFIG

    with _INIT_LOCK:
        if _INITIALIZED and _ACTIVE_PROVIDER is not None:
            logger.debug("tp_obs_v3 is already initialized. Returning existing provider.")
            return _ACTIVE_PROVIDER

        config = SDKConfig.from_env_and_kwargs(
            service=service,
            service_name=service_name,
            environment=environment,
            version=version,
            endpoint=endpoint,
            traces_endpoint=traces_endpoint,
            headers=headers,
            sample_rate=sample_rate,
            disabled=disabled,
            debug=debug,
            resource_attributes=resource_attributes,
            integrations=integrations,
            **kwargs,
        )
        # Allow explicit metrics_endpoint kwarg
        if metrics_endpoint is not None:
            config.metrics_endpoint = metrics_endpoint  # type: ignore
        elif "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT" in kwargs:
            config.metrics_endpoint = kwargs["OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"]  # type: ignore
        _ACTIVE_CONFIG = config

        if config.debug:
            logging.basicConfig(level=logging.DEBUG)
            logger.setLevel(logging.DEBUG)

        # Build Resource attributes - distinguish resource vs span attributes
        # Resource: service.name, service.version, deployment.environment.name, service.instance.id
        resource_data = {
            "service.name": config.service_name,
            "deployment.environment.name": config.environment,
            "service.version": config.version,
            "telemetry.sdk.name": "tp_obs_v3",
            "telemetry.sdk.language": "python",
            "telemetry.sdk.version": __version__,
        }
        # Backwards compat: also set old keys if user expects them
        # Preferred keys are deployment.environment.name, but keep deployment.environment for transition
        resource_data["deployment.environment"] = config.environment
        if config.resource_attributes:
            resource_data.update(config.resource_attributes)
        # Add service.instance.id if available (hostname/pid)
        try:
            import socket, os
            resource_data.setdefault("service.instance.id", f"{socket.gethostname()}-{os.getpid()}")
        except Exception:
            pass
        resource = Resource.create(resource_data)

        # Configure Sampler
        if config.disabled or config.sample_rate <= 0.0:
            sampler = ALWAYS_OFF
        elif config.sample_rate >= 1.0:
            sampler = ALWAYS_ON
        else:
            sampler = ParentBased(root=TraceIdRatioBased(config.sample_rate))

        # Create TracerProvider
        provider = TracerProvider(resource=resource, sampler=sampler)

        # Configure Exporter and Processor if not disabled
        if not config.disabled:
            if exporter is None:
                otlp_endpoint = config.traces_endpoint or f"{config.endpoint.rstrip('/')}/v1/traces"
                exporter = OTLPSpanExporter(
                    endpoint=otlp_endpoint,
                    headers=config.headers or None,
                )

            if span_processor is None:
                if export_batch:
                    span_processor = BatchSpanProcessor(exporter)
                else:
                    span_processor = SimpleSpanProcessor(exporter)

            provider.add_span_processor(span_processor)

        # Set as global tracer provider
        trace.set_tracer_provider(provider)
        _ACTIVE_PROVIDER = provider

        # --- MeterProvider setup ---
        meter_provider: Optional[MeterProvider] = None
        if not config.disabled:
            readers: List[MetricReader] = []
            if metric_reader is not None:
                readers.append(metric_reader)
            elif metric_exporter is not None:
                try:
                    readers.append(PeriodicExportingMetricReader(metric_exporter, export_interval_millis=60000, export_timeout_millis=2000))
                except Exception as exc:
                    logger.debug("Failed to create metric reader: %s", exc)
            else:
                # Default: if tracing uses InMemorySpanExporter (tests), use InMemoryMetricReader to avoid network
                try:
                    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter as _IMSE

                    if isinstance(exporter, _IMSE):
                        from opentelemetry.sdk.metrics.export import InMemoryMetricReader

                        readers.append(InMemoryMetricReader())
                    else:
                        metrics_endpoint_val = getattr(config, "metrics_endpoint", None) or f"{config.endpoint.rstrip('/')}/v1/metrics"
                        _metric_exporter = OTLPMetricExporter(
                            endpoint=metrics_endpoint_val,
                            headers=config.headers or None,
                            timeout=2,
                        )
                        readers.append(PeriodicExportingMetricReader(_metric_exporter, export_interval_millis=60000, export_timeout_millis=2000))
                except Exception as exc:
                    logger.debug("Failed to create OTLP metric exporter: %s", exc)

            if readers:
                try:
                    # Histogram buckets via View — not via instrument itself
                    views = [
                        View(
                            instrument_name="http.server.request.duration",
                            aggregation=ExplicitBucketHistogramAggregation(boundaries=_HTTP_DURATION_BOUNDARIES),
                        )
                    ]
                    meter_provider = MeterProvider(resource=resource, metric_readers=readers, views=views)
                    # set_meter_provider is Once-guarded; force override for test isolation
                    try:
                        metrics.set_meter_provider(meter_provider)
                    except Exception:
                        pass
                    # Force-set even if Once prevented (ensures InMemoryReader works after reset)
                    try:
                        import opentelemetry.metrics._internal as _mi

                        _mi._METER_PROVIDER = meter_provider  # type: ignore
                        if hasattr(_mi, "_METER_PROVIDER_SET_ONCE"):
                            _mi._METER_PROVIDER_SET_ONCE._done = True  # type: ignore
                        # Update proxy provider
                        if hasattr(_mi, "_PROXY_METER_PROVIDER"):
                            _mi._PROXY_METER_PROVIDER._real_meter_provider = meter_provider  # type: ignore
                            # Notify existing proxy meters
                            for _m in list(getattr(_mi._PROXY_METER_PROVIDER, "_meters", [])):
                                try:
                                    _m.on_set_meter_provider(meter_provider)  # type: ignore
                                except Exception:
                                    pass
                        # Also ensure metrics module's alias (if any)
                        try:
                            import opentelemetry.metrics as _m_api

                            if hasattr(_m_api, "_METER_PROVIDER"):
                                _m_api._METER_PROVIDER = meter_provider  # type: ignore
                        except Exception:
                            pass
                    except Exception:
                        pass
                    _ACTIVE_METER_PROVIDER = meter_provider
                except Exception as exc:
                    logger.debug("Failed to create MeterProvider: %s", exc)
        else:
            # Disabled: use no-op meter provider
            try:
                meter_provider = MeterProvider(resource=resource)
                try:
                    metrics.set_meter_provider(meter_provider)
                except Exception:
                    pass
                _ACTIVE_METER_PROVIDER = meter_provider
            except Exception:
                pass

        _INITIALIZED = True

        # Setup W3C Trace Context propagator
        set_global_textmap(TraceContextTextMapPropagator())

        # Register shutdown on process exit
        def _shutdown():
            try:
                provider.shutdown()
            except Exception:
                pass
            if _ACTIVE_METER_PROVIDER is not None:
                try:
                    _ACTIVE_METER_PROVIDER.shutdown()
                except Exception:
                    pass
        atexit.register(_shutdown)

        logger.info(
            "tp_obs_v3 initialized successfully (service=%s, env=%s, endpoint=%s)",
            config.service_name,
            config.environment,
            config.endpoint,
        )

        return provider


def get_config() -> Optional[SDKConfig]:
    """Return the active SDKConfig or None if not initialized."""
    return _ACTIVE_CONFIG


def patch_all(**kwargs: Any) -> List[str]:
    """
    Auto-discover and patch all installed integrations.
    
    Args:
        **kwargs: Optional overrides to enable/disable specific integrations (e.g. django=False)
        
    Returns:
        List of integration names that were successfully instrumented.
    """
    manager = get_integration_manager()
    return manager.apply_integrations(config=get_config(), **kwargs)


def _reset_for_testing() -> None:
    """Internal helper to reset singleton state between unit tests."""
    global _INITIALIZED, _ACTIVE_PROVIDER, _ACTIVE_METER_PROVIDER, _ACTIVE_CONFIG
    with _INIT_LOCK:
        if _ACTIVE_PROVIDER is not None:
            try:
                _ACTIVE_PROVIDER.shutdown()
            except Exception:
                pass
        if _ACTIVE_METER_PROVIDER is not None:
            try:
                _ACTIVE_METER_PROVIDER.shutdown()
            except Exception:
                pass
        _INITIALIZED = False
        _ACTIVE_PROVIDER = None
        _ACTIVE_METER_PROVIDER = None
        _ACTIVE_CONFIG = None
        # Uninstrument all active integrations
        try:
            get_integration_manager().uninstrument_all()
        except Exception:
            pass
        # Reset OpenTelemetry global state
        trace._TRACER_PROVIDER = None  # type: ignore
        if hasattr(trace, "_TRACER_PROVIDER_SET_ONCE"):
            trace._TRACER_PROVIDER_SET_ONCE._done = False  # type: ignore
        # Reset metrics - handle both new and old internal structures
        try:
            # Reset global provider
            metrics._METER_PROVIDER = None  # type: ignore
            if hasattr(metrics, "_METER_PROVIDER_SET_ONCE"):
                metrics._METER_PROVIDER_SET_ONCE._done = False  # type: ignore
            # Reset proxy provider state
            try:
                from opentelemetry.metrics._internal import _PROXY_METER_PROVIDER

                _PROXY_METER_PROVIDER._real_meter_provider = None  # type: ignore
                _PROXY_METER_PROVIDER._meters.clear()  # type: ignore
            except Exception:
                pass
            try:
                import opentelemetry.metrics._internal as _mi

                if hasattr(_mi, "_METER_PROVIDER"):
                    _mi._METER_PROVIDER = None  # type: ignore
                if hasattr(_mi, "_METER_PROVIDER_SET_ONCE"):
                    _mi._METER_PROVIDER_SET_ONCE._done = False  # type: ignore
                if hasattr(_mi, "_PROXY_METER_PROVIDER"):
                    _mi._PROXY_METER_PROVIDER._real_meter_provider = None  # type: ignore
                    try:
                        _mi._PROXY_METER_PROVIDER._meters.clear()  # type: ignore
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception:
            pass
        # Reset django metrics globals
        try:
            from tp_obs_v3.integrations import django as _dj

            _dj._meter = _dj._req_counter = _dj._err_counter = _dj._duration_hist = None  # type: ignore
        except Exception:
            pass


__all__ = [
    "init",
    "patch_all",
    "get_config",
    "get_tracer",
    "get_current_span",
    "get_integration_manager",
    "BaseIntegration",
    "SDKConfig",
    "sanitize_url",
    "sanitize_sql",
    "__version__",
]
