# python-otel-sdk — Framework-agnostic OpenTelemetry SDK

One SDK for **Django, FastAPI, Flask, Starlette/ASGI** + any Python app.  All config via `OTEL_*` env vars — zero hard-coded values.

## Install

```bash
pip install -e ./python-otel-sdk              # core only (psycopg2, redis, requests)
pip install -e "./python-otel-sdk[django]"    # + Django
pip install -e "./python-otel-sdk[fastapi]"   # + FastAPI / Starlette
pip install -e "./python-otel-sdk[all]"       # everything
```

## Quickstart

### Any Python app (core spans + DB/cache enrichment)

```python
from otel_sdk import init_tracing
init_tracing()  # reads OTEL_* env vars
```

### FastAPI

```python
from fastapi import FastAPI
from otel_sdk import init_tracing

app = FastAPI()
init_tracing(service_name="my-fastapi-service", frameworks=["fastapi"], app=app)
# or two-step:
# from otel_sdk.frameworks.fastapi import instrument_fastapi
# init_tracing(service_name="my-api")
# instrument_fastapi(app)
```

### Django

```python
# settings.py
MIDDLEWARE = [
    ...,
    "otel_sdk.frameworks.django.ViewTracingMiddleware",  # view spans
]

# gunicorn.conf.py  (worker-safe init)
def post_fork(server, worker):
    from otel_sdk import init_tracing
    init_tracing(service_name="django", frameworks=["django"])
```

Old import still works: `from django_otel_sdk import init_tracing` (shim).

### Flask / Starlette

```python
from flask import Flask
from otel_sdk import init_tracing
app = Flask(__name__)
init_tracing(frameworks=["flask"], app=app)

# Starlette/ASGI
from otel_sdk.frameworks.starlette import instrument_starlette
app = instrument_starlette(app)
```

## Env vars

| Var | Default | Description |
|-----|---------|-------------|
| `OTEL_ENABLED` | `true` | Disable entirely when `false` |
| `OTEL_SERVICE_NAME` | `python-app` | `service.name` |
| `OTEL_ENVIRONMENT` | `development` | `deployment.environment` |
| `OTEL_APPLICATION` | `python-otel` | `application` resource |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | Collector gRPC endpoint |
| `OTEL_FRAMEWORKS` | auto-detect | Comma list e.g. `django,fastapi` |
| `OTEL_BATCH_DELAY_MS` | `2000` | Batch flush interval |
| `OTEL_BATCH_MAX_SIZE` | `256` | Max spans per batch |
| `OTEL_BATCH_QUEUE_SIZE` | `2048` | Queue size |
| `OTEL_FLUSH_TIMEOUT_MS` | `5000` | Shutdown flush timeout |

`init_tracing()` kwargs override env vars: `init_tracing(service_name="x", frameworks=["fastapi"], app=app)`.

## Manual instrumentation

```python
from otel_sdk import traced, span, add_span_attributes

@traced
def process_order(order_id): ...

@traced(span_name="custom.work", attributes={"job": "seed"})
async def async_work(): ...

with span("db.seed", attributes={"records": 100}):
    seed_db()

add_span_attributes({"user.id": 42})
```

## Shutdown

```python
from otel_sdk import shutdown_tracing
# gunicorn worker_exit, FastAPI lifespan, etc.
shutdown_tracing()
```

## Architecture

```
otel_sdk/
  core/config.py      # OtelConfig.from_env / from_kwargs
  core/tracer.py      # TracerProvider + OTLP exporter + propagators
  sdk.py              # init_tracing() orchestration + auto-detect
  processors/db.py    # PostgresSpanProcessor (generic)
  processors/cache.py # RedisSpanProcessor
  frameworks/
    django.py         # DjangoInstrumentor + ViewTracingMiddleware + DB alias + template
    fastapi.py        # FastAPIInstrumentor.instrument_app + view span middleware
    flask.py          # FlaskInstrumentor
    starlette.py      # ASGI middleware
  decorators.py       # @traced / span() / helpers
  utils/              # query + context helpers
```
