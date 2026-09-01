"""Django OTel metrics — Counter/Histogram instruments.

Instruments (all low-cardinality):
- http.server.request.duration (Histogram, s) — stable semconv, p50/p95/p99 via histogram
- http.server.requests (Counter, custom) — total requests; could also be derived from histogram count but kept explicit for PoC
- http.server.errors (Counter, custom) — 5xx only

Histogram bucket boundaries are NOT set on the instrument itself. Configure via
SDK View + ExplicitBucketHistogramAggregation in `tp_obs_v3.init()`.

Attributes: http.request.method, http.route (templated), http.response.status_code
"""
import logging
from typing import Optional

from opentelemetry import metrics as otel_metrics

logger = logging.getLogger("tp_obs_v3.integrations.django.metrics")

_meter = None
_req_counter = None
_err_counter = None
_duration_hist = None


def get_metrics():
    """Lazily obtain meter + instruments from global MeterProvider.

    Called from request instrumentation on every request; cheap after first.
    Returns (meter, req_counter, err_counter, duration_hist) or (None,... ) on failure.
    """
    global _meter, _req_counter, _err_counter, _duration_hist
    if _meter is not None:
        return _meter, _req_counter, _err_counter, _duration_hist
    try:
        _meter = otel_metrics.get_meter("tp_obs_v3.django")
        # Custom counters — stable semconv only defines histogram for duration
        _req_counter = _meter.create_counter(
            name="http.server.requests",
            description="Total number of Django HTTP requests",
            unit="1",
        )
        _err_counter = _meter.create_counter(
            name="http.server.errors",
            description="Total number of Django HTTP requests that resulted in 5xx",
            unit="1",
        )
        _duration_hist = _meter.create_histogram(
            name="http.server.request.duration",
            description="Duration of Django HTTP requests",
            unit="s",
        )
    except Exception as exc:  # pragma: no cover
        logger.debug("Failed to create Django metrics instruments: %s", exc)
        return None, None, None, None
    return _meter, _req_counter, _err_counter, _duration_hist


def reset_metrics():  # for testing / uninstrument
    global _meter, _req_counter, _err_counter, _duration_hist
    _meter = _req_counter = _err_counter = _duration_hist = None
