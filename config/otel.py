"""tp_obs_v3 OpenTelemetry bootstrap for the Django POC sample app.

Initializes the tp_obs_v3 SDK (request waterfall tracing across middleware,
views, and templates, plus request metrics) and auto-instruments Django when
the SDK is installed. Fail-safe: the app keeps working without it.
"""
import logging
import os

logger = logging.getLogger("config.otel")

SERVICE_NAME = "django-otel-poc"


def setup_telemetry():
    """Initialize tp_obs_v3 and auto-instrument Django. Idempotent, fail-safe."""
    if os.environ.get("TP_OBS_DISABLED", "").lower() in ("1", "true", "yes"):
        logger.info("tp_obs_v3 disabled via TP_OBS_DISABLED")
        return False
    try:
        import tp_obs_v3
    except ImportError:
        logger.info("tp_obs_v3 not installed; observability disabled")
        return False
    try:
        tp_obs_v3.init(
            service=os.environ.get("TP_OBS_SERVICE_NAME", SERVICE_NAME),
            environment=os.environ.get("TP_OBS_ENVIRONMENT", "development"),
            version=os.environ.get("TP_OBS_VERSION", "0.1.0"),
        )
        enabled = tp_obs_v3.patch_all()
        logger.info("tp_obs_v3 initialized; integrations: %s", enabled)
        return True
    except Exception:
        logger.exception("tp_obs_v3 initialization failed; continuing without observability")
        return False