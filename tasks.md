# Implementation Tasks & Checklist

This document tracks all tasks across the 6 implementation phases for the OpenTelemetry Datadog APM Parity project.

---

## Phase 1: Python Dependencies & Core Observability Package
- [x] **Task 1.1**: Update `requirements.txt` with OpenTelemetry packages (`opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp`, `opentelemetry-instrumentation-django`, `opentelemetry-instrumentation-psycopg2`, `opentelemetry-instrumentation-redis`, `opentelemetry-instrumentation-requests`, `opentelemetry-instrumentation-wsgi`).
- [x] **Task 1.2**: Create `observability/__init__.py`.
- [x] **Task 1.3**: Create `observability/setup.py` with `init_tracing()` initializing TracerProvider, BatchSpanProcessor, and auto-instrumentors.
- [x] **Task 1.4**: Create `observability/django.py` implementing `instrument_middleware()` (functional `load_middleware` hook with `process_view`, `process_exception`, `process_template_response` preservation) and non-invasive `ViewTracingMiddleware`.
- [x] **Task 1.5**: Create `observability/db.py` implementing `BaseDatabaseWrapper.cursor` hook for deterministic DB alias mapping (`default` -> `postgres`, `slave1` -> `slave1db`, `slave2` -> `slave2db`, `slave3` -> `slave3db`), parameterized SQL redaction (`db.query.text`), and query summary generation (`db.query.summary`).
- [x] **Task 1.6**: Create `observability/cache.py` implementing `RedisSpanProcessor` for Redis command & resource formatting (`app.cache.operation`, `app.cache.resource`).

---

## Phase 2: Infrastructure Configuration & Docker Orchestration
- [x] **Task 2.1**: Create `otel-collector/config.yaml` with receivers, `spanmetricsconnector`, `memory_limiter`, `batch`, and exporters (`otlp/tempo`, `prometheus` on `:8889`).
- [x] **Task 2.2**: Create `tempo/config.yaml` for trace storage & search API.
- [x] **Task 2.3**: Create `prometheus/prometheus.yml` with scrape target for `otel-collector:8889`.
- [x] **Task 2.4**: Create `grafana/provisioning/datasources/datasources.yaml` registering Tempo (default) and Prometheus datasources.
- [x] **Task 2.5**: Create `grafana/provisioning/dashboards/dashboards.yaml` registering JSON dashboard provider.
- [x] **Task 2.6**: Update `docker-compose.yml` to add `otel-collector`, `tempo`, `prometheus`, and `grafana` services on the `observability` network.

---

## Phase 3: Application Integration & Gunicorn Hooks
- [x] **Task 3.1**: Update `config/wsgi.py` to import `observability.setup` before `get_wsgi_application()` and wrap application with `OpenTelemetryMiddleware`.
- [x] **Task 3.2**: Update `gunicorn.conf.py` to implement `post_fork` hook calling `observability.setup.init_tracing()` and `worker_exit` hook calling `force_flush()`.
- [x] **Task 3.3**: Build and start container stack (`docker-compose up -d --build`).

---

## Phase 4: P0 Golden Trace Gate Verification
- [x] **Task 4.1**: Execute test requests exercising Master DB write (`POST /api/products/`), Slave DB reads (`read-slave1`, `read-slave2`, `read-slave3`), Redis cache (`cache`), and External HTTP (`external`).
- [x] **Task 4.2**: Verify Golden Trace in Tempo (`http://localhost:3000` -> Explore -> Tempo):
  - [x] Single `trace_id` shared across all child spans.
  - [x] Nested middleware spans present (`SecurityMiddleware`, `CsrfViewMiddleware`, etc.).
  - [x] View span starts in `process_view` and closes in `__call__` cleanly.
  - [x] DB, Redis, and HTTP spans are valid descendants of the view path.
  - [x] `postgres`, `slave1db`, `slave2db`, `slave3db`, `redis`, and `external-api` tagged with `server.address` & `peer.service`.
  - [x] Read (`SELECT`) vs write (`INSERT`/`UPDATE`) correctly distinguished.
  - [x] Error status and exception details recorded for failure endpoints.
  - [x] Zero Django control flow or CSRF validation regressions.

---

## Phase 5: Metrics & Dashboard Provisioning
- [x] **Task 5.1**: Verify Prometheus metric targets (`http://localhost:9090/api/v1/targets` and `http://localhost:8889/metrics`).
- [x] **Task 5.2**: Create `grafana/provisioning/dashboards/json/1_service_catalog.json` (Datadog Screen 3 & 4 - Service Catalog Table).
- [x] **Task 5.3**: Create `grafana/provisioning/dashboards/json/2_service_endpoints.json` (Datadog Screen 3 - Endpoint RED Metrics Table).
- [x] **Task 5.4**: Create `grafana/provisioning/dashboards/json/3_database_resources.json` (Datadog Screen 4 - Query RED Metrics Table for Master DB, Replica DBs, and Redis).
- [x] **Task 5.5**: Create `grafana/provisioning/dashboards/json/4_trace_explorer.json` (Datadog Screen 2 - Duration Scatter Plot & Filterable Trace Table).

---

## Phase 6: Traffic Generator & Verification
- [x] **Task 6.1**: Create `scripts/generate_traffic.py` to generate realistic multi-service traffic.
- [x] **Task 6.2**: Run traffic generator and verify all 4 screens in Grafana UI (`http://localhost:3000`).
- [x] **Task 6.3**: Create `DATADOG_REPLACEMENT_GUIDE.md` blueprint for integrating this observability stack into any Django project in 5 minutes.
