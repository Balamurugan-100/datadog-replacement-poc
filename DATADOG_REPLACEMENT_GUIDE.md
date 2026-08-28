# OpenTelemetry Datadog Replacement Blueprint for Django

This guide details how this OpenTelemetry (OTel) + Tempo + Prometheus + Grafana APM stack is implemented and provides a 5-minute step-by-step blueprint for adding this exact observability capability to **any** Django application.

---

## 1. Package Architecture

```text
observability/
├── __init__.py                 # Package interface exports (init_tracing, ViewTracingMiddleware)
├── setup.py                    # OpenTelemetry SDK initialization facade
├── utils/                      # Dedicated Utility Functions
│   ├── __init__.py             # Exports for clean context and query utilities
│   ├── context.py              # ContextVar & connection host discovery
│   └── query.py                # SQL parsing, parameterization & summary generation
├── django.py                   # Django HTTP response, middleware & view tracing
├── db.py                       # PostgreSQL span processor & cursor hooks
└── cache.py                    # Redis span processor & django-redis hooks
```

---

## 2. Key Observability Features

1. **Root Application Service (`django`):** Every HTTP request is identified under service `django`.
2. **Master & Slave DB Alias Spans (`postgres`, `slave1db`, `slave2db`, `slave3db`):** Queries to Master or Replica databases appear as nested child spans tagged with their database alias.
3. **Trace Waterfall View:** Full parent-child trace hierarchy showing time spent in WSGI, individual middlewares, views, PostgreSQL queries, Redis calls, and outbound HTTP requests.
4. **Interactive DataLinks:** Clicking any endpoint route in Grafana immediately opens Tempo Explore pre-filtered to trace instances for that route.

---

## 3. Step-by-Step Integration into Any Django Application

### Step 1: Add Dependencies to `requirements.txt`
```text
opentelemetry-api==1.33.0
opentelemetry-sdk==1.33.0
opentelemetry-exporter-otlp==1.33.0
opentelemetry-instrumentation-django==0.54b0
opentelemetry-instrumentation-psycopg2==0.54b0
opentelemetry-instrumentation-redis==0.54b0
opentelemetry-instrumentation-requests==0.54b0
opentelemetry-instrumentation-wsgi==0.54b0
```

### Step 2: Copy the `observability/` Package
Copy the [`observability/`](file:///Users/bala/workspace/datadog-replacement-poc/observability) folder into your project root (alongside `manage.py`):
```text
my_django_project/
├── manage.py
├── my_django_project/
│   ├── settings.py
│   └── wsgi.py
└── observability/          <-- COPY THIS FOLDER
    ├── __init__.py
    ├── setup.py
    ├── utils/
    │   ├── context.py
    │   └── query.py
    ├── django.py
    ├── db.py
    └── cache.py
```

### Step 3: Initialize Observability in `wsgi.py`
Import `observability` before `get_wsgi_application()` in `wsgi.py`:
```python
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'my_django_project.settings')

import observability  # noqa: E402

from django.core.wsgi import get_wsgi_application
from opentelemetry.instrumentation.wsgi import OpenTelemetryMiddleware

application = OpenTelemetryMiddleware(get_wsgi_application())
```

### Step 4: Add Middleware to `settings.py`
Append `ViewTracingMiddleware` to `MIDDLEWARE` in `settings.py`:
```python
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # ... your existing middlewares ...
    "observability.ViewTracingMiddleware",  # Traces view execution
]
```

### Step 5: Configure Gunicorn Hooks in `gunicorn.conf.py`
```python
def post_fork(server, worker):
    try:
        import observability
        observability.init_tracing()
    except Exception as e:
        server.log.error(f"OTel post_fork init failed: {e}")

def worker_exit(server, worker):
    try:
        from opentelemetry import trace
        provider = trace.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush(timeout_millis=3000)
    except Exception:
        pass
```

### Step 6: Configure Environment Variables
Set these variables in your deployment / `docker-compose.yml`:
```yaml
OTEL_SERVICE_NAME=my-django-service
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
```
