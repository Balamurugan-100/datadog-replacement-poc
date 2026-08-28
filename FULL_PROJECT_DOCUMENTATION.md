# Full Project Documentation: OpenTelemetry APM Stack for Django (Datadog Replacement PoC)

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Problem Statement](#2-problem-statement)
3. [Solution Architecture](#3-solution-architecture)
4. [Complete Directory Structure](#4-complete-directory-structure)
5. [Technology Stack & Tool Glossary](#5-technology-stack--tool-glossary)
6. [How It All Connects — Data Flow](#6-how-it-all-connects--data-flow)
7. [Django Application Deep Dive](#7-django-application-deep-dive)
8. [The `observability/` Package — Core Innovation](#8-the-observability-package--core-innovation)
9. [Infrastructure Services](#9-infrastructure-services)
10. [Configuration Reference](#10-configuration-reference)
11. [API Endpoints Reference](#11-api-endpoints-reference)
12. [Grafana Dashboards](#12-grafana-dashboards)
13. [Traffic Generator](#13-traffic-generator)
14. [Quick Start](#14-quick-start)
15. [Integrating Into Your Own Django Project](#15-integrating-into-your-own-django-project)
16. [Troubleshooting](#16-troubleshooting)

---

## 1. Project Overview

This project is a **proof-of-concept replacement for Datadog APM** (Application Performance Monitoring). It demonstrates how to achieve Datadog-equivalent observability capabilities — service catalog, endpoint monitoring, trace waterfalls, database/cache visibility, and RED metrics — using **entirely open-source tools** with zero vendor lock-in.

The sample application is a Django REST API backed by:
- **4 PostgreSQL databases** (1 master + 3 replicas) to simulate a real multi-database topology
- **Redis** for caching
- **External HTTP calls** to a mock API service

Every single operation — HTTP requests, middleware execution, view logic, database queries to each specific database, Redis cache hits/misses, and outbound HTTP calls — produces rich, correlated trace data that flows through an OpenTelemetry pipeline into Grafana for visualization.

---

## 2. Problem Statement

Datadog APM provides excellent observability but at significant cost and with vendor lock-in. This PoC answers:

> **Can we replicate Datadog's APM capabilities (service catalog, trace waterfalls, per-service metrics, database/cache drill-down) using only open-source tools?**

The answer is yes, using:
- **OpenTelemetry** for instrumentation (vendor-neutral standard)
- **OTel Collector** as the telemetry pipeline
- **Grafana Tempo** for distributed trace storage
- **Prometheus** for RED metrics (Rate, Errors, Duration)
- **Grafana** for dashboards and visualization

---

## 3. Solution Architecture

```
                         Client HTTP Request (:8001)
                                    |
                                    v
                  +-----------------------------------------+
                  |          Nginx Reverse Proxy              |
                  |  Listens on :80 | Proxies to web:8000    |
                  +-----------------------------------------+
                                    |
                                    v
                  +-----------------------------------------+
                  |      Gunicorn + Django Application       |
                  |                                          |
                  |  wsgi.py                                 |
                  |    -> imports observability.setup        |
                  |       (initializes OTel SDK)             |
                  |    -> wraps app with                     |
                  |       OpenTelemetryMiddleware            |
                  |                                          |
                  |  gunicorn.conf.py                        |
                  |    post_fork: re-init OTel per worker    |
                  |    worker_exit: force-flush spans        |
                  |                                          |
                  |  Middleware Stack:                        |
                  |    SecurityMiddleware (traced)            |
                  |    SessionMiddleware (traced)             |
                  |    CommonMiddleware (traced)              |
                  |    CsrfViewMiddleware (traced)            |
                  |    AuthenticationMiddleware (traced)      |
                  |    MessageMiddleware (traced)             |
                  |    XFrameOptionsMiddleware (traced)       |
                  |    RequestLoggingMiddleware (custom)      |
                  |    ViewTracingMiddleware (custom)         |
                  |                                          |
                  |  Custom Span Processors:                 |
                  |    PostgresSpanProcessor (enriches DB)   |
                  |    RedisSpanProcessor (enriches cache)   |
                  +-----------------------------------------+
                         |        |        |        |
            +------------+   +----+----+   |   +----+------+
            v                v           v   v             v
      +----------+    +-----------+ +---------+ +----------+ +---------+
      | PgBouncer|    | slave1db  | | slave2db| | slave3db | |  Redis  |
      |  :6432   |    |  :5434   | |  :5435  | |  :5436   | |  :6379  |
      +----+-----+    +-----------+ +---------+ +----------+ +---------+
           v
      +----------+
      | postgres |
      |  :5433   |
      +----------+

                         |
                         | OTLP gRPC (:4317)
                         v
                  +-----------------------------------------+
                  |      OpenTelemetry Collector              |
                  |                                           |
                  |  Receiver: OTLP (gRPC :4317, HTTP :4318) |
                  |  Processor: memory_limiter + batch        |
                  |  Connector: spanmetrics (traces -> RED)   |
                  |  Exporter: otlp/tempo, prometheus, debug   |
                  +-----------------------------------------+
                         |                    |
              OTLP Traces              Prometheus Metrics
                         |                    |
                         v                    v
               +--------------+      +--------------+
               | Grafana      |      | Prometheus   |
               | Tempo        |      |              |
               | (traces)     |      | (metrics)    |
               +--------------+      +--------------+
                         |                    |
                         +--------+-----------+
                                  |
                                  v
                        +--------------+
                        |   Grafana    |
                        |   UI :3000   |
                        +--------------+
```

---

## 4. Complete Directory Structure

```
datadog-replacement-poc/
|
|-- README.md                              # Quick start guide
|-- FULL_PROJECT_DOCUMENTATION.md          # This file
|-- DATADOG_REPLACEMENT_GUIDE.md           # 5-min integration blueprint
|-- design.md                              # Technical design spec
|-- tasks.md                               # Implementation checklist
|-- requirements.txt                       # Python dependencies (24 packages)
|-- Dockerfile                             # Application container (Python 3.8.18-slim)
|-- docker-compose.yml                     # Full 13-service Docker stack
|-- gunicorn.conf.py                       # Gunicorn config with OTel hooks
|-- manage.py                              # Django management entry point
|-- mise.toml                              # Python version pinning (3.8.18)
|-- .gitignore                             # Git ignore rules
|
|-- config/                                # Django project configuration
|   |-- __init__.py
|   |-- settings.py                        # Django settings (4 DBs, Redis, DRF)
|   |-- urls.py                            # Root URL configuration
|   |-- wsgi.py                            # WSGI entry point (OTel integration)
|   +-- asgi.py                            # ASGI entry point (unused for OTel)
|
|-- api/                                   # Django "api" application
|   |-- __init__.py
|   |-- apps.py                            # App configuration
|   |-- models.py                          # Product model
|   |-- serializers.py                     # DRF ProductSerializer
|   |-- views.py                           # All views (367 lines)
|   |-- urls.py                            # API URL routing
|   |-- admin.py                           # Django admin registration
|   |-- middleware.py                       # RequestLoggingMiddleware
|   |-- tests.py                           # Placeholder (no tests)
|   |-- migrations/
|   |   |-- __init__.py
|   |   +-- 0001_initial.py                # Product table migration
|   |-- management/
|   |   +-- commands/
|   |       +-- setup_databases.py         # Migrate + seed all 4 DBs
|   +-- templates/
|       |-- dashboard.html                 # Interactive OTel testing dashboard
|       +-- products/
|           |-- list.html                  # HTML product list
|           +-- detail.html                # HTML product detail
|
|-- observability/                         # *** CORE OBSERVABILITY PACKAGE ***
|   |-- __init__.py                        # Exports: init_tracing, ViewTracingMiddleware
|   |-- setup.py                           # TracerProvider init + auto-instrumentors
|   |-- django.py                          # Middleware + view + template tracing
|   |-- db.py                              # PostgresSpanProcessor + cursor hooks
|   |-- cache.py                           # RedisSpanProcessor + django-redis hooks
|   +-- utils/
|       |-- __init__.py                    # Re-exports utilities
|       |-- context.py                     # ContextVar for DB alias + cache namespace
|       +-- query.py                       # SQL cleaning + parameterization
|
|-- scripts/
|   +-- generate_traffic.py                # Weighted random traffic generator
|
|-- otel-collector/
|   +-- config.yaml                        # OTel Collector pipeline config
|
|-- tempo/
|   +-- config.yaml                        # Grafana Tempo config
|
|-- prometheus/
|   +-- prometheus.yml                     # Prometheus scrape config
|
|-- grafana/
|   +-- provisioning/
|       |-- datasources/
|       |   +-- datasources.yaml           # Tempo + Prometheus datasources
|       +-- dashboards/
|           |-- dashboards.yaml            # Dashboard provider config
|           +-- json/
|               |-- 1_service_catalog.json           # Dashboard 1
|               |-- 2_datadog_apm.json               # Dashboard 2
|               +-- 3_datadog_service_detail.json    # Dashboard 3
|
|-- pgbouncer/
|   |-- pgbouncer.ini                      # PgBouncer config (session mode)
|   |-- userlist.txt                       # Plain auth: "django"/"django"
|   +-- pg_hba.conf                        # Client auth rules
|
|-- nginx/
|   |-- Dockerfile                         # nginx:1.25-alpine
|   +-- nginx.conf                         # Reverse proxy config
|
+-- staticfiles/                           # Collected Django static files (gitignored)
```

---

## 5. Technology Stack & Tool Glossary

### 5.1 Application Layer

| Tool | Version | What It Is | Why This Project Uses It |
|------|---------|------------|--------------------------|
| **Python** | 3.8.18 | General-purpose programming language | Runtime for Django and all instrumentation code |
| **Django** | 4.2.25 | High-level Python web framework | Provides the web application being monitored (models, views, ORM, middleware) |
| **Django REST Framework (DRF)** | 3.14.0 | Toolkit for building Web APIs on top of Django | Provides `ModelViewSet`, serializers, pagination, and API routing for the Product resource |
| **Gunicorn** | 19.10.0 | Python WSGI HTTP Server | Production-grade server that runs Django; chosen for its `gthread` worker class and fork hooks that enable per-worker OTel initialization |
| **psycopg2-binary** | 2.8.6 | PostgreSQL adapter for Python | Connects Django to all 4 PostgreSQL databases; the binary variant avoids requiring libpq-dev at runtime |
| **django-redis** | 4.12.1 | Redis cache backend for Django | Provides `django_redis.client.DefaultClient` which this project monkey-patches for tracing |
| **redis** | 3.0.1 | Python Redis client library | Low-level Redis connection library used by django-redis |
| **requests** | 2.27.1 | HTTP library for Python | Used by the app to make outbound HTTP calls (to external-api / go-httpbin) |
| **sqlparse** | 0.5.5 | SQL parser for Python | Provides SQL normalization utilities (used indirectly) |

### 5.2 Observability / Instrumentation

| Tool | Version | What It Is | Why This Project Uses It |
|------|---------|------------|--------------------------|
| **opentelemetry-api** | 1.33.0 | OpenTelemetry API specification | Defines the interfaces (`trace.get_tracer`, `Span`, `Context`) that all instrumentation code uses |
| **opentelemetry-sdk** | 1.33.0 | OpenTelemetry SDK implementation | Provides `TracerProvider`, `BatchSpanProcessor`, `Resource`, and the actual span creation/export machinery |
| **opentelemetry-exporter-otlp** | 1.33.0 | OTLP exporter for OpenTelemetry | Exports spans over gRPC to the OTel Collector at `:4317` |
| **opentelemetry-instrumentation-django** | 0.54b0 | Auto-instrumentation for Django | Creates HTTP request/response spans automatically; this project uses it with `is_middleware_instrumentation_enabled=False` (custom middleware tracing instead) |
| **opentelemetry-instrumentation-psycopg2** | 0.54b0 | Auto-instrumentation for psycopg2 | Automatically creates spans for every SQL query executed through psycopg2 cursors |
| **opentelemetry-instrumentation-redis** | 0.54b0 | Auto-instrumentation for Redis | Automatically creates spans for Redis commands |
| **opentelemetry-instrumentation-requests** | 0.54b0 | Auto-instrumentation for `requests` library | Automatically creates spans for every outbound HTTP call made via `requests.get/post/etc.` |
| **opentelemetry-instrumentation-wsgi** | 0.54b0 | Auto-instrumentation for WSGI | Wraps the Django WSGI application with `OpenTelemetryMiddleware` to extract/create root traces from incoming HTTP requests |

### 5.3 Infrastructure (Docker)

| Tool | Version | What It Is | Why This Project Uses It |
|------|---------|------------|--------------------------|
| **PostgreSQL** | 16 | Open-source relational database | Primary data store; 4 instances simulate a master-replica topology (default, slave1, slave2, slave3) |
| **PgBouncer** | latest | PostgreSQL connection pooler | Sits in front of the master PostgreSQL; demonstrates that the OTel instrumentation correctly identifies the real database host even through a proxy |
| **Redis** | 7 | In-memory data structure store | Caching layer; the project traces every cache hit/miss with operation type and key namespace |
| **Nginx** | 1.25-alpine | High-performance web server | Reverse proxy in front of Gunicorn; serves static files directly; maps to port 8001 on the host |
| **OTel Collector** | contrib 0.121.0 | OpenTelemetry telemetry pipeline | Receives traces from the Django app, processes them (batching, memory limiting), converts traces to RED metrics via spanmetrics connector, and exports to Tempo + Prometheus |
| **Grafana Tempo** | 2.6.1 | Distributed tracing backend | Stores all trace data; supports TraceQL queries and trace waterfall visualization |
| **Prometheus** | latest | Time-series metrics database | Stores RED metrics (request rate, error rate, latency) generated from traces by the OTel Collector's spanmetrics connector |
| **Grafana** | 11.4.0 | Visualization and dashboarding platform | Provides 3 pre-built APM dashboards that replicate Datadog's service catalog, APM overview, and service detail views |
| **go-httpbin** | v2.14.0 | HTTP request/response testing service | Acts as a mock "external API" that the Django app calls, producing outbound HTTP trace spans |

### 5.4 Tooling

| Tool | What It Is | Why This Project Uses It |
|------|------------|--------------------------|
| **mise** (via `mise.toml`) | Version manager for development tools (formerly rtx/asdf) | Pins Python to exactly 3.8.18 across development environments |
| **Docker Compose** | Multi-container Docker application orchestrator | Defines and runs all 13 services with networking, volumes, health checks, and dependency ordering |

---

## 6. How It All Connects — Data Flow

### 6.1 Request Lifecycle (Step by Step)

Here is exactly what happens when a user hits `GET /api/products/read-slave2/`:

```
1. Client sends HTTP GET to localhost:8001

2. Nginx receives request on :80
   - Checks if path starts with /static/ -> serve from volume
   - Otherwise, proxy_pass to upstream "django" (web:8000)

3. Gunicorn worker picks up the request on :8000
   - Request enters the WSGI application

4. OpenTelemetryMiddleware (from opentelemetry-instrumentation-wsgi)
   - Extracts W3C TraceContext headers (or creates a new root trace)
   - Creates a root span: "GET" (later renamed by response hook)

5. Django middleware stack executes (each wrapped with tracing by instrument_middleware()):
   - SecurityMiddleware           -> span: "django.middleware.security.SecurityMiddleware"
   - SessionMiddleware            -> span: "django.contrib.sessions.middleware.SessionMiddleware"
   - CommonMiddleware             -> span: "django.middleware.common.CommonMiddleware"
   - CsrfViewMiddleware           -> span: "django.middleware.csrf.CsrfViewMiddleware"
   - AuthenticationMiddleware     -> span: "django.contrib.auth.middleware.AuthenticationMiddleware"
   - MessageMiddleware            -> span: "django.contrib.messages.middleware.MessageMiddleware"
   - XFrameOptionsMiddleware      -> span: "django.middleware.clickjacking.XFrameOptionsMiddleware"
   - RequestLoggingMiddleware     -> (not traced by OTel, but adds X-Request-ID + structured JSON log)
   - ViewTracingMiddleware.__call__ -> waits for get_response

6. URL routing resolves to ProductViewSet.read_slave2

7. ViewTracingMiddleware.process_view fires:
   - Creates span: "view.ProductViewSet.read_slave2"
   - Attaches it to the request context

8. ProductViewSet.read_slave2 executes:
   Product.objects.using("slave2").all()

9. Django ORM opens cursor on "slave2" database:
   - instrument_django_db_aliases() hook fires on BaseDatabaseWrapper._cursor
   - Sets ContextVar: _active_db_service_var = "slave2db"

10. psycopg2 auto-instrumentation creates a span for the SQL query:
    - PostgresSpanProcessor.on_start reads ContextVar -> sets server.address="slave2db"
    - PostgresSpanProcessor.on_end renames span to: "postgres.query SELECT * FROM api_product"
    - Adds attributes: db.operation.name="SELECT", db.query.summary, db.row_count

11. Response flows back up:
    - ViewTracingMiddleware.__call__ closes the view span, sets http.status_code
    - Each middleware span closes
    - Root span gets renamed by django_response_hook to: "GET ^api/products/read-slave2/$"
    - Root span gets http.status_code=200

12. OTel SDK BatchSpanProcessor batches the trace and exports via OTLP gRPC to otel-collector:4317

13. OTel Collector:
    - Receives the trace
    - Memory limiter checks (512MB cap)
    - Batch processor groups spans (200ms timeout, batch size 64)
    - Exports traces to Tempo via OTLP gRPC
    - spanmetrics connector computes RED metrics from the trace
    - Exports metrics to Prometheus via Prometheus exporter (:8889)

14. Grafana:
    - Queries Prometheus for RED metrics (dashboards show req/sec, P95, error rate)
    - Queries Tempo for trace waterfalls (clicking a trace shows the full parent-child tree)
```

### 6.2 Telemetry Data Model

```
                         SERVICE
                    django (service.name)
                           |
          +----------------+----------------+
          v                                 v
    HTTP RESOURCES                     DEPENDENCIES
 (GET /api/products/)           (server.address / peer.service)
                                       |
  +---------+----------+----------+----+-----------+----------+
  v         v          v          v    v           v          v
postgres  slave1db   slave2db  slave3db redis  external-api  ...
  |         |          |          |    |           |
  v         v          v          v    v           v
SQL       SQL        SQL        SQL  Cache       HTTP
Resources Resources  Resources  Res  Resource   Resource
(db.q.sum)(db.q.sum)(db.q.sum)(db.q)(app.c.res) (http.route)
```

**Key span attributes produced by this project:**

| Attribute | Example Value | Produced By |
|-----------|---------------|-------------|
| `service.name` | `django` | TracerProvider resource |
| `server.address` | `slave2db` | PostgresSpanProcessor + ContextVar |
| `peer.service` | `slave2db` | PostgresSpanProcessor |
| `db.system` | `postgresql` | Psycopg2Instrumentor + PostgresSpanProcessor |
| `db.operation.name` | `SELECT` | PostgresSpanProcessor.on_end |
| `db.query.summary` | `SELECT * FROM api_product WHERE id=?` | PostgresSpanProcessor.on_end |
| `db.query.text` | `SELECT * FROM api_product WHERE id = 1` | PostgresSpanProcessor.on_end |
| `db.row_count` | `2` | instrument_psycopg2_rowcount |
| `http.method` | `GET` | django_response_hook |
| `http.route` | `^api/products/read-slave2/$` | django_response_hook |
| `http.status_code` | `200` | django_response_hook |
| `app.cache.operation` | `GET` | RedisSpanProcessor / instrument_django_redis |
| `app.cache.resource` | `products` | extract_cache_resource_namespace |
| `cache.key` | `products:all_cached` | instrument_django_redis |
| `span.type` | `db`, `cache`, `web` | Various span processors |

---

## 7. Django Application Deep Dive

### 7.1 `config/settings.py` — Project Configuration

The Django settings file configures **4 PostgreSQL databases**:

```python
DATABASES = {
    "default": { "HOST": "pgbouncer", "PORT": 6432, "NAME": "django_otel" },
    "slave1":  { "HOST": "slave1db",  "PORT": 5432, "NAME": "django_otel_slave1" },
    "slave2":  { "HOST": "slave2db",  "PORT": 5432, "NAME": "django_otel_slave2" },
    "slave3":  { "HOST": "slave3db",  "PORT": 5432, "NAME": "django_otel_slave3" },
}
```

The `default` database routes through **PgBouncer** (a connection pooler) before hitting the master PostgreSQL. The slave databases connect directly. This simulates a real production topology where reads go to replicas.

**Redis cache** is configured via `django-redis`:
```python
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://redis:6379/1",
    }
}
```

The middleware stack includes `RequestLoggingMiddleware` (custom structured logging) but **not** `ViewTracingMiddleware` in the settings — that is injected by the `instrument_middleware()` hook at runtime.

### 7.2 `config/wsgi.py` — OTel Entry Point

This is the **most critical file** for observability integration:

```python
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import observability.setup  # <-- THIS initializes OTel BEFORE Django loads

from django.core.wsgi import get_wsgi_application
from opentelemetry.instrumentation.wsgi import OpenTelemetryMiddleware

application = OpenTelemetryMiddleware(get_wsgi_application())
```

**Order matters**: `import observability.setup` runs `init_tracing()` at module import time, which creates the `TracerProvider`, installs all span processors, and sets up all auto-instrumentors **before** Django's WSGI app is fully initialized. This ensures all hooks are in place when Django loads its middleware, models, and URL routing.

The `OpenTelemetryMiddleware` wrapping extracts/creates root traces from incoming HTTP requests.

### 7.3 `gunicorn.conf.py` — Per-Worker OTel Lifecycle

```python
def post_fork(server, worker):
    """Called after each worker process forks from the master."""
    import observability.setup
    observability.setup.init_tracing()  # Re-init OTel in child process

def worker_exit(server, worker):
    """Called when a worker is shutting down."""
    provider = trace.get_tracer_provider()
    provider.force_flush(timeout_millis=3000)  # Ensure all spans are exported
```

**Why per-worker init?** OpenTelemetry's SDK maintains process-specific state (background threads for batch export, process IDs). After Gunicorn forks worker processes, each worker must re-initialize the SDK to get its own independent `TracerProvider` with its own `BatchSpanProcessor` and export thread. Without this, only one worker would export spans, or spans would be lost.

**Why force-flush?** When Gunicorn recycles a worker (e.g., after `max_requests`), `worker_exit` ensures any spans still in the batch buffer are exported before the process dies.

### 7.4 `api/models.py` — Product Model

```python
class Product(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

A simple model used across all 4 databases. The `setup_databases` management command seeds 2 sample products into each database at container startup.

### 7.5 `api/views.py` — Application Logic (367 lines)

The views are designed to exercise **every type of operation** that would need observability in a real application:

**ProductViewSet (DRF ModelViewSet)** — The main API with custom actions:

| Action | Method | What It Does | Why It Exists |
|--------|--------|--------------|---------------|
| `list` | GET `/api/products/` | Lists all products from master DB | Standard CRUD |
| `create` | POST `/api/products/` | Creates a product on master DB | Standard CRUD |
| `read_slave1/2/3` | GET | Reads from specific slave DBs using `.using("slaveN")` | Demonstrates DB alias tracking in traces |
| `cache` | GET `/api/products/cache/` | Checks Redis cache, falls back to DB | Demonstrates cache hit/miss tracing |
| `external` | GET `/api/products/external/` | Makes outbound HTTP call to go-httpbin | Demonstrates HTTP client tracing |
| `error` | GET `/api/products/error/` | Raises `RuntimeError` deliberately | Demonstrates error tracing |
| `slow` | GET `/api/products/slow/` | Sleeps 2 seconds, then queries DB | Demonstrates latency visualization |
| `cached` | GET `/api/products/{id}/cached/` | Per-product cache with DB fallback | Per-resource caching pattern |
| `bulk_create` | POST | Creates multiple products in a transaction | Demonstrates transaction tracing |
| `adjust_stock` | POST | Uses `select_for_update()` for atomic update | Demonstrates lock contention tracing |
| `health` | GET | Checks all 4 PGs + Redis | Multi-dependency health check |

**Standalone views:**

| View | Path | Purpose |
|------|------|---------|
| `ExternalCallView` | `/api/external/` | Generic HTTP proxy |
| `DBTransactionView` | `/api/db-tx/` | Multi-statement database transaction |
| `ThreadedView` | `/api/threaded/` | Parallel thread execution |
| `RawSQLView` | `/api/raw-sql/` | Execute arbitrary SQL |
| `ManualSpanView` | `/api/manual-span/` | Create manual parent/child OTel spans |
| `DashboardView` | `/` | Interactive testing dashboard UI |

### 7.6 `api/middleware.py` — RequestLoggingMiddleware

```python
class RequestLoggingMiddleware:
    def __call__(self, request):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request._request_id = request_id

        start = time.perf_counter()
        response = self.get_response(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)

        logger.info(json.dumps({
            "request_id": request_id,
            "method": request.method,
            "path": request.path,
            "status": response.status_code,
            "duration_ms": duration_ms,
        }))

        response["X-Request-ID"] = request_id
        response["X-Response-Time"] = f"{duration_ms}ms"
        return response
```

This middleware is **not instrumented by OTel** (it is not wrapped by `instrument_middleware()` because it's added after the hook fires). It provides:
- **Structured JSON logging** to stdout (parseable by log aggregators)
- **Correlation ID** propagation via `X-Request-ID` header
- **Response time** header for client-side visibility
- **Exception handling** via `process_exception`

### 7.7 `api/management/commands/setup_databases.py` — Startup Command

Runs automatically when the `web` container starts (before Gunicorn):

```python
def handle(self, *args, **options):
    for database_alias in settings.DATABASES.keys():  # default, slave1, slave2, slave3
        call_command("migrate", database=database_alias, interactive=False)
        if Product.objects.using(database_alias).count() == 0:
            # Seed 2 sample products per database
            Product.objects.using(database_alias).create(...)
```

---

## 8. The `observability/` Package — Core Innovation

This is the reusable package that makes the whole system work. It can be copied into **any Django project** for instant Datadog-equivalent APM.

### 8.1 `observability/setup.py` — Initialization Facade

Called automatically on import. Two phases:

**Phase 1: `_init_tracer_provider()`**
1. Creates a `TracerProvider` with resource attributes (`service.name=django`, `deployment.environment`)
2. Adds `PostgresSpanProcessor` — enriches database spans with alias-based service names
3. Adds `RedisSpanProcessor` — enriches Redis spans with operation/resource info
4. Creates `BatchSpanProcessor` -> `OTLPSpanExporter` (gRPC to `:4317`, batch size 64, 200ms delay)
5. Sets up W3C TraceContext + Baggage propagation (for distributed tracing)

**Phase 2: `_init_instrumentors()`**
1. `instrument_django_db_aliases()` — hooks `BaseDatabaseWrapper._cursor`
2. `instrument_psycopg2_rowcount()` — hooks `cursor.execute`/`executemany`
3. `instrument_django_redis()` — wraps 9 `DefaultClient` methods
4. `instrument_middleware()` — wraps `BaseHandler.load_middleware`
5. `instrument_template_render()` — wraps `Template.render`
6. `DjangoInstrumentor().instrument()` — auto HTTP spans (middleware tracing disabled)
7. `Psycopg2Instrumentor().instrument()` — auto SQL spans
8. `RedisInstrumentor().instrument()` — auto Redis spans
9. `RequestsInstrumentor().instrument()` — auto outbound HTTP spans

### 8.2 `observability/db.py` — PostgreSQL Instrumentation

**`PostgresSpanProcessor(SpanProcessor)`** — A custom span processor that runs on every span:

```python
def on_start(self, span, parent_context=None):
    # Reads the ContextVar set by instrument_django_db_aliases()
    # Sets server.address and peer.service on the span BEFORE it's recorded
    connection_target_service = get_active_db_context()
    span._attributes["server.address"] = connection_target_service
    span._attributes["peer.service"] = connection_target_service

def on_end(self, span):
    # Renames span to "postgres.query SELECT * FROM api_product WHERE id=?"
    # Adds db.operation.name, db.query.summary, db.query.text
    # Sets server.port, db.system, component, span.type
```

**`instrument_django_db_aliases()`** — Monkey-patches Django's `BaseDatabaseWrapper._cursor`:

```python
def cursor_traced(self, *args, **kwargs):
    # self is the database wrapper, self.alias is "default"/"slave1"/etc.
    connection_target_service = get_service_name_for_connection(self)
    # Maps: default -> postgres, slave1 -> slave1db, slave2 -> slave2db, etc.
    set_active_db_context(connection_target_service)
    return original_cursor_method(self, *args, **kwargs)
```

This is the key mechanism that makes each database query appear under the correct service in Grafana.

**`instrument_psycopg2_rowcount()`** — Monkey-patches `psycopg2.extensions.cursor.execute` and `executemany` to capture `db.row_count` after each query.

### 8.3 `observability/cache.py` — Redis Instrumentation

**`RedisSpanProcessor(SpanProcessor)`** — Enriches auto-instrumented Redis spans:

```python
def on_end(self, span):
    # Only processes spans from Psycopg2Instrumentor (db.system == "redis")
    # Skips spans already created by instrument_django_redis (name starts with "django_redis.")
    # Renames to "redis GET", sets server.address="redis", app.cache.operation="GET"
```

**`instrument_django_redis()`** — Wraps 9 methods on `django_redis.client.DefaultClient`:

```python
# Methods wrapped: get, set, delete, get_many, set_many, incr, decr, touch, clear
# Each creates a child span with:
#   - span name: "django_redis.cache.get"
#   - attributes: db.system=redis, cache.key="products:all_cached", app.cache.resource="products"
```

### 8.4 `observability/django.py` — Django HTTP + Middleware + Template Tracing

**`django_response_hook(span, request, response)`** — Called by `DjangoInstrumentor` after each request:

```python
# Renames root span from generic "GET" to specific "GET ^api/products/read-slave2/$"
# Sets: http.method, http.route, http.status_code, http.url, http.host, etc.
# Attaches request._request_id as span attribute
```

**`instrument_middleware()`** — The most complex hook. Intercepts Django's `BaseHandler.load_middleware`:

```python
def load_middleware_traced(self, *args, **kwargs):
    # Temporarily replaces base.import_string with a traced version
    # When Django loads each middleware class, the traced version:
    #   1. Imports the middleware class normally
    #   2. Wraps it in a factory that creates a traced closure
    #   3. The traced closure creates a span for each request
    #   4. Preserves process_view, process_exception, process_template_response hooks
    #   5. Preserves sync_capable/async_capable attributes
```

This creates **nested spans for every middleware** in the stack, giving per-middleware latency visibility.

**`instrument_template_render()`** — Wraps `Template.render` to trace template rendering:

```python
def render_traced(self, context):
    template_span_name = f"django.template.render {template_name}"
    with _template_tracer.start_as_current_span(template_span_name) as span:
        span.set_attribute("template.name", template_name)
        return original_render_method(self, context)
```

**`ViewTracingMiddleware`** — A standard Django middleware for view-level spans:

```python
def process_view(self, request, view_func, view_args, view_kwargs):
    # Creates span: "view.ProductViewSet.read_slave2"
    # Attaches it to request._otel_view_span
    # Returns None (lets Django invoke the view normally)

def __call__(self, request):
    response = self.get_response(request)
    # Closes the view span
    # Sets http.status_code, marks errors for 4xx/5xx
```

### 8.5 `observability/utils/context.py` — Context Variable Management

Uses Python's `contextvars.ContextVar` (thread-safe, async-safe) to track which database is active:

```python
_active_db_service_var = contextvars.ContextVar("db_alias", default="postgres")

def get_service_name_for_connection(connection):
    # Priority: 1) configured HOST env var, 2) alias-based mapping
    # alias "default" -> "postgres"
    # alias "slave1"  -> "slave1db"
    # alias "slave2"  -> "slave2db"
    # alias "slave3"  -> "slave3db"

def extract_cache_resource_namespace(cache_key):
    # "products:all_cached" -> "products"
    # "product:1" -> "product"
```

### 8.6 `observability/utils/query.py` — SQL Processing Utilities

```python
def clean_sql_statement(raw_sql):
    # "  SELECT  *   FROM api_product  " -> "SELECT * FROM api_product"

def extract_sql_operation(cleaned_sql):
    # "SELECT * FROM api_product WHERE id = 1" -> "SELECT"

def parameterize_sql_summary(cleaned_sql):
    # "SELECT * FROM api_product WHERE id = 1" -> "SELECT * FROM api_product WHERE id = ?"
    # Removes numeric literals to create low-cardinality summaries safe for metrics

def format_postgres_span_name(query_summary):
    # "SELECT * FROM api_product WHERE id = ?" -> "postgres.query SELECT * FROM api_product WHERE id = ?"
```

---

## 9. Infrastructure Services

### 9.1 PostgreSQL Instances

| Service | Container Name | Host Port | DB Name | Role |
|---------|---------------|-----------|---------|------|
| `postgres` | django-otel-postgres | 5433 | django_otel | Master (writes) |
| `slave1db` | django-otel-slave1db | 5434 | django_otel_slave1 | Replica 1 |
| `slave2db` | django-otel-slave2db | 5435 | django_otel_slave2 | Replica 2 |
| `slave3db` | django-otel-slave3db | 5436 | django_otel_slave3 | Replica 3 |

All use `postgres:16` image, user `django`, password `django`. Each has a health check (`pg_isready`) and a named volume for persistent data.

### 9.2 PgBouncer

Routes connections for `django_otel` database to `postgres:5432`. Configuration:
- **Pool mode**: session (each client gets a dedicated server connection for the session)
- **Max client connections**: 1000
- **Default pool size**: 500
- **Auth**: plain text via userlist.txt (`"django" "django"`)

PgBouncer sits between Django and the master PostgreSQL. The OTel instrumentation correctly identifies the real database host (`postgres`) through the proxy by reading the `HOST` setting from Django's database config.

### 9.3 Redis

Single instance on port 6379. Used by Django's cache framework via `django-redis`. Health check: `redis-cli ping`.

### 9.4 Nginx

Reverse proxy configuration:
- Listens on port 80 (mapped to host port 8001)
- Serves `/static/` directly from the `static_files` volume (with 30-day cache)
- Proxies all other requests to `web:8000` with standard proxy headers
- 120s read timeout, 10s connect timeout

### 9.5 OTel Collector

Image: `otel/opentelemetry-collector-contrib:0.121.0`

**Pipeline architecture:**
```
Receivers        Processors           Connectors        Exporters
---------        ----------           ----------        ---------
otlp (gRPC:4317) -> memory_limiter -> spanmetrics ---+-> otlp/tempo (traces to Tempo)
otlp (HTTP:4318) -> batch         ---+              +-> debug (console logging)
                                      +-> prometheus (metrics to Prometheus :8889)
```

**Spanmetrics connector configuration:**
- Histogram buckets: 2ms, 5ms, 10ms, 25ms, 50ms, 100ms, 250ms, 500ms, 1s, 2.5s, 5s, 10s
- 10 dimensions: http.method, http.status_code, http.route, server.address, peer.service, db.system, db.operation.name, db.query.summary, app.cache.operation, app.cache.resource

This means Prometheus gets metrics like:
- `otel_http_request_duration_milliseconds_count{http_method="GET",http_route="^api/products/read-slave2/$",...}`
- `otel_http_request_duration_milliseconds_bucket{le="100",...}`

### 9.6 Grafana Tempo

- **Storage backend**: Local filesystem (`/var/tempo/blocks`)
- **WAL**: Write-ahead log for durability
- **Metrics generator**: Produces `service_graphs` (service dependency map) and `span_metrics` (RED metrics per span) from incoming traces
- **OTLP receiver**: Accepts traces on gRPC :4317 and HTTP :4318

### 9.7 Prometheus

- Scrapes `otel-collector:8889` every 2 seconds
- Stores all metrics with the `otel_` namespace prefix (e.g., `otel_http_request_duration_milliseconds_*`)

### 9.8 Grafana

- Pre-provisioned with **Tempo** (default datasource) and **Prometheus** datasources
- Auto-loads 3 dashboards from JSON files on startup
- Anonymous auth enabled with Admin role (no login required for PoC)
- Admin password: `admin`

### 9.9 go-httpbin

Image: `mccutchen/go-httpbin:v2.14.0`

A Go implementation of HTTPBin. The Django app's `external` endpoint calls `http://external-api:8080/get` which produces outbound HTTP trace spans. This simulates calling a real third-party API.

---

## 10. Configuration Reference

### 10.1 Environment Variables (Django Container)

| Variable | Default | Purpose |
|----------|---------|---------|
| `DJANGO_DB_HOST` | `localhost` | Master PostgreSQL host (set to `pgbouncer` in Docker) |
| `DJANGO_DB_PORT` | `5433` | Master PostgreSQL port |
| `DJANGO_DB_NAME` | `django_otel` | Master database name |
| `DJANGO_DB_USER` | `django` | Database user |
| `DJANGO_DB_PASSWORD` | `django` | Database password |
| `SLAVE1_DB_HOST` | `localhost` | Slave 1 PostgreSQL host |
| `SLAVE1_DB_PORT` | `5434` | Slave 1 port |
| `SLAVE2_DB_HOST` | `localhost` | Slave 2 PostgreSQL host |
| `SLAVE2_DB_PORT` | `5435` | Slave 2 port |
| `SLAVE3_DB_HOST` | `localhost` | Slave 3 PostgreSQL host |
| `SLAVE3_DB_PORT` | `5436` | Slave 3 port |
| `EXTERNAL_API_URL` | `http://localhost:8080/get` | Mock external API URL |
| `DJANGO_REDIS_URL` | `redis://127.0.0.1:6379/1` | Redis connection URL |
| `GUNICORN_WORKERS` | `cpu_count * 2 + 1` | Number of Gunicorn workers |
| `GUNICORN_THREADS` | `4` | Threads per worker |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | OTel Collector gRPC endpoint |
| `OTEL_SERVICE_NAME` | `django` | Service name in traces |
| `ENVIRONMENT` | `development` | Deployment environment tag |

### 10.2 Docker Compose Networks

| Network | Purpose | Services |
|---------|---------|----------|
| `default` (bridge) | Application networking | postgres, pgbouncer, redis, slave1-3, external-api, web, nginx |
| `observability` (bridge) | Telemetry pipeline | All above + otel-collector, tempo, prometheus, grafana |

All services connect to both networks so the Django app can reach the OTel Collector, and Grafana can reach Tempo and Prometheus.

### 10.3 Docker Compose Volumes

| Volume | Purpose |
|--------|---------|
| `postgres_data` | Master PostgreSQL data |
| `slave1_data` | Slave 1 PostgreSQL data |
| `slave2_data` | Slave 2 PostgreSQL data |
| `slave3_data` | Slave 3 PostgreSQL data |
| `static_files` | Django collected static files (shared between web and nginx) |
| `pgbouncer_log` | PgBouncer logs |
| `tempo-data` | Tempo trace storage |
| `grafana-data` | Grafana configuration/state |

---

## 11. API Endpoints Reference

Base URL: `http://localhost:8001` (via Nginx)

### Products CRUD

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/products/` | List all products (paginated, 20 per page) |
| POST | `/api/products/` | Create a new product |
| GET | `/api/products/{id}/` | Retrieve a single product |
| PUT | `/api/products/{id}/` | Update a product |
| PATCH | `/api/products/{id}/` | Partial update |
| DELETE | `/api/products/{id}/` | Delete a product |

### Topology & Database

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/products/read-slave1/` | Read products from slave1 database |
| GET | `/api/products/read-slave2/` | Read products from slave2 database |
| GET | `/api/products/read-slave3/` | Read products from slave3 database |
| GET | `/api/products/health/` | Health check all 4 PGs + Redis |

### Caching

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/products/cache/` | Redis cache hit/miss test |
| GET | `/api/products/{id}/cached/` | Per-product cached fetch |

### External & Network

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/products/external/` | Outbound HTTP call to go-httpbin |
| GET | `/api/external/` | Generic HTTP proxy (pass `?url=`) |
| GET | `/api/webhook/` | Webhook receiver (POST only) |

### Transactions & Concurrency

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/products/bulk_create/` | Create multiple products in transaction |
| POST | `/api/products/{id}/adjust_stock/` | Atomic stock adjustment with `select_for_update` |
| POST | `/api/db-tx/` | Multi-statement database transaction |
| GET | `/api/threaded/` | Parallel thread execution (`?threads=3&delay=0.5`) |

### Observability Testing

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/products/error/` | Deliberate 500 error |
| GET | `/api/products/slow/` | 2-second artificial delay |
| GET | `/api/manual-span/` | Create manual OTel parent/child spans |
| GET | `/api/raw-sql/` | Execute raw SQL (`?query=SELECT 1`) |

### Debug & Info

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/metrics/` | Aggregated product statistics |
| GET | `/api/db-info/` | PostgreSQL version and connection info |
| GET | `/api/cache-stats/` | Redis cache backend info |

### UI

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Interactive OTel testing dashboard |
| GET | `/api/products-tmpl/` | HTML product list page |
| GET | `/api/products-tmpl/{id}/` | HTML product detail page |
| GET | `/admin/` | Django Admin (admin/admin) |

---

## 12. Grafana Dashboards

Three pre-provisioned dashboards that replicate Datadog's APM views:

### Dashboard 1: Service Catalog (`apm-overview`)

**UID**: `apm-overview`
**File**: `grafana/provisioning/dashboards/json/1_service_catalog.json`

Panels:
- **Services & Dependency Overview Table**: Lists all services (django, postgres, slave1db, slave2db, slave3db, redis) with real-time Req/sec, P95 Latency, and Error Rate
- **Incoming Request Endpoints Table**: Every HTTP route with throughput and latency
- **Throughput & Latency Time Series**: Charts with service variable dropdown
- Deep-links from endpoint routes to Tempo trace explorer

### Dashboard 2: Datadog APM Overview (`datadog-apm-replacement`)

**UID**: `datadog-apm-replacement`
**File**: `grafana/provisioning/dashboards/json/2_datadog_apm.json`

Panels:
- **Services Catalog**: Req/sec, P95, Error Rate per service
- **Aggregated Request & Operation Summary**: Unified view of HTTP endpoints + DB queries + Redis operations
- **Trace Instances Table**: Filterable trace list with TraceQL filtering and Tempo deep-links

### Dashboard 3: Service Detail (`datadog-service-detail`)

**UID**: `datadog-service-detail`
**File**: `grafana/provisioning/dashboards/json/3_datadog_service_detail.json`

Panels:
- **Health Status** stat panel
- **Throughput / P95 / P99 / Error Rate** stat panels
- **Throughput, Latency, Error Rate** time series charts
- **Downstream Service Call Relationships** table
- **Request Breakdown** (endpoints for the selected service)
- **Trace Instances** with Tempo waterfall deep-links

---

## 13. Traffic Generator

**File**: `scripts/generate_traffic.py`

A standalone Python script that generates realistic weighted traffic to populate the Grafana dashboards:

```bash
python3 scripts/generate_traffic.py --duration 30 --delay 0.3
```

**Weight distribution:**

| Endpoint | Weight | Approximate % |
|----------|--------|---------------|
| Cache (Redis hit/miss) | 12 | 17% |
| Product list (Master DB) | 10 | 14% |
| Read Slave 1/2/3 (8 each) | 24 | 34% |
| Health check (all DBs + Redis) | 5 | 7% |
| External HTTP call | 6 | 9% |
| Slow endpoint (2s delay) | 3 | 4% |
| Error endpoint (500) | 2 | 3% |

The script uses `random.choices()` with weights to simulate realistic traffic patterns where cache operations and replica reads are most frequent.

---

## 14. Quick Start

### Prerequisites
- Docker and Docker Compose installed
- Python 3.8+ (for the traffic generator script)

### Start the Stack

```bash
# Clean start (removes all volumes and containers)
docker-compose down -v

# Build and start all 13 services
docker-compose up -d --build
```

Wait ~30 seconds for all services to initialize (database migrations, health checks, etc.).

### Access the UI

| Service | URL |
|---------|-----|
| **Grafana APM Dashboard** | http://localhost:3000/d/apm-overview/apm-services-and-request-overview |
| **Grafana Trace Explorer** | http://localhost:3000/explore |
| **Prometheus** | http://localhost:9090 |
| **OTel Collector Health** | http://localhost:13133 |
| **Django App (Dashboard)** | http://localhost:8001/ |
| **Django API** | http://localhost:8001/api/products/ |
| **Django Admin** | http://localhost:8001/admin/ (admin/admin) |

### Generate Traffic

```bash
python3 scripts/generate_traffic.py --duration 60
```

Then open Grafana to see the dashboards populate with real data.

### Explore Traces

1. Open Grafana at http://localhost:3000
2. Click any endpoint route in the dashboard tables
3. Grafana opens Tempo Explore filtered to that route
4. Click any trace instance to see the full waterfall:
   - WSGI -> Middlewares (nested) -> View -> DB queries -> Redis calls -> HTTP calls

---

## 15. Integrating Into Your Own Django Project

### Step 1: Add Dependencies

Add to your `requirements.txt`:
```
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

Copy the `observability/` folder into your project root (alongside `manage.py`):
```
my_project/
  manage.py
  my_project/
    settings.py
    wsgi.py
  observability/     <-- COPY THIS
    __init__.py
    setup.py
    utils/
      context.py
      query.py
    django.py
    db.py
    cache.py
```

### Step 3: Modify `wsgi.py`

```python
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'my_project.settings')

import observability  # noqa: E402

from django.core.wsgi import get_wsgi_application
from opentelemetry.instrumentation.wsgi import OpenTelemetryMiddleware

application = OpenTelemetryMiddleware(get_wsgi_application())
```

### Step 4: Modify `gunicorn.conf.py`

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

### Step 5: Set Environment Variables

```bash
OTEL_SERVICE_NAME=my-service
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
```

### Step 6: Customize the DB Alias Map (if needed)

Edit `observability/utils/context.py` to add your database aliases:

```python
def get_service_name_for_connection(connection):
    connection_alias = getattr(connection, "alias", "default")
    # Add your custom aliases here
    ALIAS_MAP = {
        "default": "primary-db",
        "analytics": "analytics-db",
        # ...
    }
    return ALIAS_MAP.get(connection_alias, connection_alias)
```

---

## 16. Troubleshooting

### No traces appearing in Grafana

1. Check OTel Collector health: `curl http://localhost:13133`
2. Check OTel Collector logs: `docker logs django-otel-collector`
3. Verify `OTEL_EXPORTER_OTLP_ENDPOINT` is set correctly in the web container
4. Check Gunicorn worker logs for OTel initialization errors

### Dashboards showing "No data"

1. Generate traffic first: `python3 scripts/generate_traffic.py --duration 30`
2. Check Prometheus targets: http://localhost:9090/targets
3. Verify the otel-collector:8889 target is UP
4. Check Grafana datasource configuration: http://localhost:3000/datasources

### Database connection errors

1. Check PostgreSQL health: `docker logs django-otel-postgres`
2. Check PgBouncer: `docker logs django-otel-pgbouncer`
3. Verify the `setup_databases` command ran: `docker logs django-otel-web | grep "Setting up"`

### High memory usage

The OTel Collector is configured with a 512MB memory limit. If this is too low:
```yaml
# otel-collector/config.yaml
processors:
  memory_limiter:
    limit_mib: 1024  # Increase as needed
```

---

*This document covers every component, configuration, and integration point in the project. For the 5-minute integration blueprint, see `DATADOG_REPLACEMENT_GUIDE.md`. For the technical design spec, see `design.md`.*
