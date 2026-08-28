# OpenTelemetry APM Stack for Django (Datadog Replacement PoC)

An open-source, high-performance APM replacement for Datadog built with **OpenTelemetry Python SDK**, **OTel Collector**, **Grafana Tempo**, **Prometheus**, and **Grafana**.

---

## 1. Quick Start

### Start Fresh Stack
```bash
docker-compose down -v
docker-compose up -d --build
```

### Generate APM Traffic
```bash
python3 scripts/generate_traffic.py --duration 30
```

---

## 2. Access Links

- **Grafana APM Overview (Default Home)**: `http://localhost:3000/d/datadog-apm-replacement/apm-datadog-replacement`
- **Grafana Service Detail**: `http://localhost:3000/d/datadog-service-detail/apm-service-detail`
- **Grafana Request Instances**: `http://localhost:3000/d/request-instances/apm-request-instances`
- **Grafana Tempo Trace Explore**: `http://localhost:3000/explore`
- **Prometheus Metrics Engine**: `http://localhost:9090`
- **OTel Collector Health Check**: `http://localhost:13133`
- **Django Application (via Nginx)**: `http://localhost:8001/api/products/`

---

## 3. How to Use the APM Features

### A. View Services & Endpoints Overview
Start at the **[Grafana APM Overview](http://localhost:3000/d/datadog-apm-replacement/apm-datadog-replacement)** (Grafana home):
- **Services**: Req/sec, P95, and Error Rate for `django`, `postgres`, `slave1db`, `slave2db`, `slave3db`, and `redis`. Click a service Name for Service Detail.
- **HTTP endpoints**, database queries, and cache operations summary on the same page.

### B. View Trace Instances & Interactive Waterfalls
1. Click an **endpoint URL** or the **View Instances** button (e.g. `GET /api/products/cache/`).
2. Grafana opens the **Request Instances** dashboard, filtered to that service + endpoint, with a table of matching traces (Trace ID, start time, duration, status).
3. Click a **Trace ID** to open the Tempo waterfall:
   - **Parent-Child Tree**: Shows WSGI → Middlewares → Django View → Master/Slave DB queries → Redis calls → Outbound HTTP API requests.
   - **Gantt Timeline**: Visualizes execution timing and latency per layer.
   - **Details Drawer**: Displays span attributes (`server.address`, `db.query.text`, `http.status_code`, etc.).

---

## 4. Architectural Highlights

1. **Per-Middleware Spans**: Custom functional interception of `BaseHandler.load_middleware` creates nested spans for every middleware (`SecurityMiddleware`, `CsrfViewMiddleware`, etc.) while preserving Django hook methods (`process_view`, `process_exception`, `process_template_response`).
2. **Master & Slave DB Alias Tagging**: Uses `contextvars.ContextVar` inside `BaseDatabaseWrapper.cursor` to map Django database connection aliases (`default` → `postgres`, `slave1` → `slave1db`, `slave2` → `slave2db`, `slave3` → `slave3db`).
3. **Parameterized SQL Redaction**: Parameterized query text (`db.query.text`) and low-cardinality query summaries (`db.query.summary`) are stored safely without PII leak risks.
4. **Gunicorn Thread Safety**: Initialized in `gunicorn.conf.py` `post_fork` hook with automatic batch flushing on `worker_exit`.

---

## 5. Documentation Guides
- [`OTEL_DJANGO_INSTRUMENTATION.md`](OTEL_DJANGO_INSTRUMENTATION.md): Request-path span tree, custom Django hooks, and how to add instrumentation.
- [`DATADOG_REPLACEMENT_GUIDE.md`](DATADOG_REPLACEMENT_GUIDE.md): 5-minute blueprint for integrating this observability stack into any Django project.
- [`design.md`](file:///Users/bala/workspace/datadog-replacement-poc/design.md): Detailed technical design and telemetry data model.
- [`tasks.md`](file:///Users/bala/workspace/datadog-replacement-poc/tasks.md): Implementation phase execution checklist.