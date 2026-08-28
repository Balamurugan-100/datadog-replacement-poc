# Architecture & Technical Design Specification

This document details the architectural design for the OpenTelemetry-based APM stack replacing Datadog APM for Django applications with multiple database backends, Redis, and external HTTP dependencies.

---

## 1. System Topology & Data Flow

```
                      Client HTTP Request (Port 8001)
                                     │
                                     ▼
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                            Nginx Reverse Proxy                          │
 │  ├── Listens on Port 8001                                               │
 │  └── Proxies HTTP requests to Gunicorn (web:8000)                        │
 └───────────────────────────────────┬─────────────────────────────────────┘
                                     │ HTTP Proxy
                                     ▼
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                   Gunicorn + Django Application                         │
 │  ├── gunicorn.conf.py post_fork Hook (Worker thread-safe OTel init)     │
 │  ├── WSGI OpenTelemetryMiddleware (Extracts/creates root trace)          │
 │  ├── BaseHandler.load_middleware Hook (Nested middleware spans)         │
 │  ├── ViewTracingMiddleware (Non-invasive process_view span creation)    │
 │  ├── BaseDatabaseWrapper.cursor Hook (Deterministic DB alias mapping)   │
 │  ├── RedisSpanProcessor (Redis command & key resource formatting)       │
 │  └── gunicorn.conf.py worker_exit Hook (Force-flushes trace batches)    │
 └───────────────────────────────────┬─────────────────────────────────────┘
                                     │ OTLP gRPC (:4317)
                                     ▼
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                        OpenTelemetry Collector                          │
 │  ├── Receivers: otlp (gRPC :4317, HTTP :4318)                          │
 │  ├── Processors: memory_limiter (512MB), transform, batch               │
 │  ├── Connectors: spanmetricsconnector (Converts traces to RED metrics)  │
 │  └── Exporters: otlp/tempo (tempo:4317), prometheus (0.0.0.0:8889)      │
 └───────────┬─────────────────────────────────────────┬───────────────────┘
             │ OTLP Traces                             │ Prom Metrics
             ▼                                         ▼
   ┌──────────────────┐                      ┌──────────────────┐
   │  Grafana Tempo   │                      │    Prometheus    │
   │ (Trace Waterfall │                      │  (RED Metric     │
   │  & TraceQL)      │                      │   TimeSeries)    │
   └─────────┬────────┘                      └─────────┬────────┘
             │                                         │
             └───────────────────┬─────────────────────┘
                                 ▼
                     ┌───────────────────────┐
                     │      Grafana UI       │
                     │  (4 APM Dashboards)   │
                     └───────────────────────┘
```

---

## 2. Telemetry Data Model

```
                              SERVICE
                         django (service.name)
                                │
           ┌────────────────────┴────────────────────┐
           ▼                                         ▼
     HTTP RESOURCES                            DEPENDENCIES
  (POST /api/products/)                   (server.address / peer.service)
                                                     │
       ┌──────────────┬──────────────┬───────────────┼──────────────┬──────────────┐
       ▼              ▼              ▼               ▼              ▼              ▼
    postgres       slave1db       slave2db        slave3db        redis       external-api
       │              │              │               │              │              │
       ▼              ▼              ▼               ▼              ▼              ▼
  SQL Resources  SQL Resources  SQL Resources   SQL Resources  Cache Resource  HTTP Resource
 (db.query.sum) (db.query.sum) (db.query.sum)  (db.query.sum)  (app.cache.res)  (http.route)
```

### Key Attributes
- **`service.name`**: `"django"` (identifies the application process).
- **`server.address` / `peer.service`**: `"postgres"`, `"slave1db"`, `"slave2db"`, `"slave3db"`, `"redis"`, `"external-api"`.
- **`db.query.text`**: Parameterized SQL query template (`SELECT * FROM users WHERE id = %s`).
- **`db.query.summary`**: Low-cardinality query summary (`SELECT users WHERE id=?`).
- **`db.operation.name`**: `SELECT`, `INSERT`, `UPDATE`, `DELETE`.
- **`app.cache.operation` / `app.cache.resource`**: `GET`, `user_session`.

---

## 3. Core Instrumentation Components

### A. Dynamic DB Alias Cursor Hook ([`observability/db.py`](file:///Users/bala/workspace/datadog-replacement-poc/observability/db.py))
Hooking Django's `BaseDatabaseWrapper.cursor` provides deterministic database alias mapping:
```python
ALIAS_MAP = {
    "default": "postgres",
    "slave1": "slave1db",
    "slave2": "slave2db",
    "slave3": "slave3db",
}
```
At cursor creation time, `self.alias` is mapped directly to `server.address` and `peer.service`.

### B. Functional Middleware Interception ([`observability/django.py`](file:///Users/bala/workspace/datadog-replacement-poc/observability/django.py))
Hooks `BaseHandler.load_middleware` to return functional closures `traced_middleware(request)` tagged with `f"{mw_cls.__module__}.{mw_cls.__qualname__}"` while preserving:
- `process_view`
- `process_exception`
- `process_template_response`
- `sync_capable`
- `async_capable`

### C. Non-Invasive View Span Lifecycle ([`observability/django.py`](file:///Users/bala/workspace/datadog-replacement-poc/observability/django.py))
`ViewTracingMiddleware.process_view` creates and attaches the `view.<ClassName>.<action>` span and returns `None` (letting Django invoke the view naturally). `__call__` closes the view span cleanly.

### D. Gunicorn Fork & Shutdown Hooks ([`gunicorn.conf.py`](file:///Users/bala/workspace/datadog-replacement-poc/gunicorn.conf.py))
- `post_fork(server, worker)`: Calls `observability.setup.init_tracing()` to give each worker process its own background export thread.
- `worker_exit(server, worker)`: Calls `trace.get_tracer_provider().force_flush()` to ensure zero span loss during worker recycles.

---

## 4. OTel Collector & Metrics Connector

The OpenTelemetry Collector (`otel/opentelemetry-collector-contrib:0.121.0`) uses `spanmetricsconnector` to connect `traces` to `metrics`:

```yaml
connectors:
  spanmetrics:
    histogram:
      explicit:
        buckets: [2ms, 5ms, 10ms, 25ms, 50ms, 100ms, 250ms, 500ms, 1s, 2.5s, 5s, 10s]
    dimensions:
      - name: http.method
      - name: http.status_code
      - name: http.route
      - name: server.address
      - name: peer.service
      - name: db.system
      - name: db.operation.name
      - name: db.query.summary
      - name: app.cache.operation
      - name: app.cache.resource
```

---

## 5. Grafana Dashboard Hierarchy (4 Datadog Screens)

1. **Dashboard 1: Service Catalog (`1_service_catalog.json`)**
   - Overview table listing `django`, `postgres`, `slave1db`, `slave2db`, `slave3db`, `redis` with Requests/sec, P95 latency, and Error Rate %.
2. **Dashboard 2: Service Endpoints (`2_service_endpoints.json`)**
   - Endpoint RED metrics table listing HTTP routes with request throughput, total execution time, P95 latency, and error rate.
3. **Dashboard 3: Database & Cache Resources (`3_database_resources.json`)**
   - Query RED metrics table for Master DB, Replica DBs, and Redis listing `db.query.summary` and `app.cache.resource`.
4. **Dashboard 4: Trace Explorer & Waterfall (`4_trace_explorer.json`)**
   - Duration Scatter Plot & Filterable Trace Instance Table with deep-links directly into Tempo Trace Waterfall view.
