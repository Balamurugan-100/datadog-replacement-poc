# tp_obs_v3

OpenTelemetry-based Python Observability SDK. Drop-in instrumentation for Django applications with automatic waterfall tracing.

## Features

- **Django instrumentation** — request, view, template, and management command spans with full parent-child hierarchy
- **W3C distributed tracing** — automatic traceparent header propagation
- **PII-safe** — URL sanitization and SQL parameter masking
- **One-line bootstrap** — `tp_obs_v3.init()` + `tp_obs_v3.patch_all()`
- **Reversible** — clean uninstrument with `uninstrument()`

## Quick Start

```python
import tp_obs_v3

tp_obs_v3.init(
    service="my-django-app",
    endpoint="http://localhost:4318",
    environment="production",
)
tp_obs_v3.patch_all()
```

## Traced Spans

| Span | Kind | Description |
|------|------|-------------|
| `django.request` | SERVER | Root span for every HTTP request. Includes `http.method`, `http.status_code`, `http.route`, `resource.name`. |
| `django.view.<name>` | INTERNAL | View function execution. Resolves DRF viewsets, class-based views, and plain functions. |
| `django.template: <name>` | INTERNAL | Template rendering. Filters out noisy internal templates (debug toolbar, form widgets). |
| `django.command.<name>` | SERVER | Management command execution. |

## Configuration

All settings are configurable via `tp_obs_v3.init()` kwargs or environment variables:

| Param | Env Var | Default | Description |
|-------|---------|---------|-------------|
| `service` | `OTEL_SERVICE_NAME` | `python-service` | Service name |
| `endpoint` | `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4318` | OTLP collector endpoint |
| `environment` | `OTEL_ENVIRONMENT` | `production` | Deployment environment |
| `sample_rate` | `OTEL_SAMPLE_RATE` | `1.0` | Trace sample rate (0.0–1.0) |
| `disabled` | `OTEL_DISABLED` | `false` | Disable all tracing |
| `debug` | `TP_OBS_DEBUG` | `false` | Enable debug logging |

## Response Headers

Every traced response includes:
- `X-Trace-ID` — hex trace ID for correlation
- `X-Span-ID` — hex span ID for this request

## Requirements

- Python 3.9+
- Django 3.2+
- `opentelemetry-api` / `opentelemetry-sdk` / `opentelemetry-exporter-otlp-proto-http`
