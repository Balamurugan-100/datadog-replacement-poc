# OpenTelemetry + Custom Django Instrumentation

How tracing works in this repo: boot, request span tree, export, and how to add more. This matches the **current code**, not older notes in `FULL_PROJECT_DOCUMENTATION.md`.

Related: [`DATADOG_REPLACEMENT_GUIDE.md`](DATADOG_REPLACEMENT_GUIDE.md) (copy into another Django app), [`design.md`](design.md) (data model).

---

## 1. What OpenTelemetry is doing here

OTel is not a Django logger. It is a pipeline:

1. **Create spans** while the request runs (HTTP, middleware, view, SQL, Redis, outbound HTTP).
2. **Attach them to one trace** via context (parent/child).
3. **Batch-export** them over OTLP gRPC to the collector (`:4317`).
4. Collector **writes traces to Tempo** and **turns spans into RED metrics** (`spanmetrics` → Prometheus). Grafana reads both.

A **span** is one timed operation. A **trace** is the tree of spans for one request. Dashboards care about attributes like `http.route`, `server.address`, `db.query.summary` — that is why this project spends so much code **renaming and enriching** spans, not just creating them.

---

## 2. Boot: when tracing is turned on

Gunicorn forks workers. The OTel SDK keeps **background export threads** and process-local state, so init must happen **after fork**, not in the master.

```python
# gunicorn.conf.py
def post_fork(server, worker):
    from observability.setup import init_tracing
    init_tracing()

def worker_exit(server, worker):
    # force_flush so spans still in the batch buffer are not dropped
    ...
```

`config/wsgi.py` only wraps the Django WSGI app with `OpenTelemetryMiddleware`. It does **not** call `init_tracing()`.

`init_tracing()` is idempotent (`_initialized`) and can be disabled with `OTEL_ENABLED=false`.

Environment (all optional):

| Variable | Default | Role |
|----------|---------|------|
| `OTEL_ENABLED` | `true` | Kill switch |
| `OTEL_SERVICE_NAME` | `django` | `service.name` on the resource |
| `OTEL_ENVIRONMENT` | `development` | `deployment.environment` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | Collector gRPC |
| `OTEL_BATCH_DELAY_MS` | `2000` | Batch flush interval |
| `OTEL_BATCH_MAX_SIZE` | `256` | Max spans per export |
| `OTEL_BATCH_QUEUE_SIZE` | `2048` | In-memory queue |
| `OTEL_FLUSH_TIMEOUT_MS` | `5000` | `worker_exit` flush |

---

## 3. Phase 1: SDK (`_init_tracer_provider`)

This is the **export side**. Nothing Django-specific yet. Implemented in `observability/setup.py`.

1. `Resource`: `service.name` + `deployment.environment`.
2. `TracerProvider` with **three processors**, in order:
   1. `PostgresSpanProcessor` — rewrite Postgres spans when they **end**
   2. `RedisSpanProcessor` — rewrite Redis command spans when they **end**
   3. `BatchSpanProcessor` + `OTLPSpanExporter` → collector
3. Global propagator: W3C Trace Context + Baggage (incoming `traceparent` continues a trace; outgoing `requests` injects it).

Processors run on **every** span and filter with `db.system`. That is the seam for “make auto-instrumentation look like Datadog” without wrapping every query.

They mutate `span._name` / `span._attributes` on a `ReadableSpan` in `on_end` (private SDK API; same pattern as OTel contrib). `PostgresSpanProcessor.on_start` is empty; Django alias mapping happens in `on_end` via a `ContextVar` set when Django opens a cursor.

---

## 4. Phase 2: instrumentors (`_init_instrumentors`)

Order is intentional: **custom monkey-patches first**, then official instrumentors.

| Step | Module | What |
|------|--------|------|
| `instrument_django_db_aliases()` | `observability/db.py` | Patch `BaseDatabaseWrapper._cursor`; remember which Django alias is active |
| `instrument_psycopg2_rowcount()` | `observability/db.py` | Patch `cursor.execute` / `executemany`; set `db.row_count` on the current span |
| `instrument_django_redis()` | `observability/cache.py` | Wrap `DefaultClient.get/set/delete/...`; cache-level spans |
| `instrument_middleware()` | `observability/django.py` | Patch `BaseHandler.load_middleware`; one span per middleware `__call__` |
| `instrument_template_render()` | `observability/django.py` | Patch `Template.render` |
| `DjangoInstrumentor().instrument(...)` | official | HTTP span + `django_response_hook`; **official middleware tracing off** |
| `Psycopg2Instrumentor().instrument()` | official | SQL spans |
| `RedisInstrumentor().instrument()` | official | Raw Redis command spans |
| `RequestsInstrumentor().instrument()` | official | Outbound HTTP spans |

Then WSGI `OpenTelemetryMiddleware` (`config/wsgi.py`) creates the **incoming HTTP** span when a request hits the worker.

`is_middleware_instrumentation_enabled=False` is deliberate: OTel’s built-in middleware spans are replaced by `instrument_middleware()`.

`ViewTracingMiddleware` is listed in `settings.MIDDLEWARE`. `instrument_middleware()` **skips wrapping it by class path** so it does not get a generic `__call__` span on top of its own view span.

---

## 5. One request as a span tree

Example: `GET /api/products/read-slave2/`.

```
OpenTelemetryMiddleware          SERVER span  (later renamed by django_response_hook)
  SecurityMiddleware             django.middleware.security.SecurityMiddleware
  SessionMiddleware
  ...
  RequestLoggingMiddleware       wrapped like the others (sets request._request_id)
  ViewTracingMiddleware.__call__ not wrapped; skipped by name
    process_view → view.ProductViewSet.read_slave2   ← current span
      postgres.query SELECT * FROM ...               ← child, peer.service=slave2db
      (optional redis / requests children)
    __call__ ends the view span
  django_response_hook on the HTTP span
```

### HTTP span (`django_response_hook`)

After the view, `request.resolver_match.route` is known. The generic `"GET"` becomes `GET ^api/products/read-slave2/$`, plus `http.route`, `http.method`, `http.status_code`, `component=django`, `span.type=web`, and `request.id` if present.

### View span (`ViewTracingMiddleware`)

Two-phase on purpose:

- `process_view` runs **after URL resolve** and **around the view**, not around the whole middleware onion. It `start_span` + `context.attach` so SQL/Redis nest **under the view**. Returning `None` lets Django invoke the view.
- `__call__` runs `get_response`, then `end()`s the span, sets `http.status_code`, and marks 4xx/5xx as errors.

DRF viewsets: span name is `view.{ClassName}.{action}` when `view_func.actions` exists; otherwise `view.{ClassName}.{http_method}`.

### Database path

1. ORM uses `Product.objects.using("slave2")`.
2. `instrument_django_db_aliases` on `_cursor` calls `get_service_name_for_connection`:
   - host not localhost → use `HOST` (e.g. Docker `slave2db`, `pgbouncer`)
   - alias `default` → `postgres`
   - alias `slave2` → `slave2db`
3. That value is stored in a `ContextVar` (`observability/utils/context.py`).
4. `Psycopg2Instrumentor` creates the SQL span.
5. `instrument_psycopg2_rowcount` sets `db.row_count`.
6. `PostgresSpanProcessor.on_end` rewrites name to `postgres.query <summary>` and sets `server.address`, `peer.service`, `db.operation.name`, `db.query.summary`, `db.query.text`, `span.type=db`.

Grafana service maps split “postgres vs slave2db” because of `server.address` / `peer.service`. Collector transform also rewrites `pgbouncer` → `postgres`.

### Cache path

`cache.get("products:all_cached")`:

- Wrapper span `django_redis.cache.get` with `cache.key`, `app.cache.resource=products` (prefix before `:`), `app.cache.operation=GET`.
- Raw Redis commands still get a span from `RedisInstrumentor`. `RedisSpanProcessor` **skips** names starting with `django_redis.`.

### Template path

`Template.render` → span `django.template.render <name>` with `template.name`.

---

## 6. After the process: collector → Grafana

```
Django BatchSpanProcessor
  → otel-collector :4317
      traces: memory_limiter → resource/cleanup → transform → batch
        → Tempo
        → spanmetrics connector
      metrics: spanmetrics → Prometheus :8889
  → Grafana dashboards
```

`spanmetrics` dimensions (must exist on spans to appear in RED charts):

- `http.method`, `http.status_code`, `http.route`
- `server.address`, `peer.service`
- `db.system`, `db.operation.name`, `db.query.summary`
- `app.cache.operation`, `app.cache.resource`

A new attribute on traces only is **not** enough for those dashboards; add it under `connectors.spanmetrics.dimensions` in `otel-collector/config.yaml`.

---

## 7. Package map

```
observability/
  setup.py          init_tracing, TracerProvider, official instrumentors
  django.py         response hook, middleware wrap, templates, ViewTracingMiddleware
  db.py             PostgresSpanProcessor, alias ContextVar hook, rowcount
  cache.py          RedisSpanProcessor, django_redis wrappers
  utils/context.py  ContextVar + alias → service name
  utils/query.py    SQL clean / operation / parameterized summary
```

`observability/__init__.py` exports `init_tracing` and `ViewTracingMiddleware`.

Copying this package into another Django app is realistic if you keep **post-fork init** and the **WSGI wrap**.

---

## 8. How to add more

Three patterns already used in this repo. Prefer the smallest one that fits.

### 8.1 Official instrumentor (Celery, Kafka, boto3, …)

In `_init_instrumentors()`, after custom patches:

```python
from opentelemetry.instrumentation.celery import CeleryInstrumentor
CeleryInstrumentor().instrument()
```

Add the package to `requirements.txt`. No Django middleware.

### 8.2 Datadog-like names on auto-spans (same as Postgres/Redis)

1. Optional: monkey-patch the client/Django API and set a `ContextVar` (who am I talking to?).
2. Add a `SpanProcessor` that on `on_end` filters by `db.system` or span name and rewrites `_name` / `_attributes`.
3. Register it in `_init_tracer_provider` **before** the OTLP batch processor.
4. If Grafana should break down by a new field, add it to `spanmetrics.dimensions`.

### 8.3 Domain / view span (same as `ManualSpanView`)

```python
from opentelemetry import trace

tracer = trace.get_tracer(__name__)
with tracer.start_as_current_span("checkout.authorize") as span:
    span.set_attribute("order.id", order_id)
    # children created here nest automatically
```

Use this for **business** work. Do not wrap SQL by hand; psycopg2 already covers that.

Demo endpoint: `GET /api/manual-span/` (`api/views.py`).

### 8.4 New Django middleware as its own span

Put the class in `MIDDLEWARE`. `instrument_middleware()` wraps `__call__` unless the path is `ViewTracingMiddleware`. If you add `process_view` / `process_exception` / `process_template_response`, the wrapper copies those hooks through.

### 8.5 Another Django hook (signals, storage, custom QuerySet)

Same as `instrument_template_render`: wrap the method, `start_as_current_span`, set attributes, call original, set `_is_otel_traced` so init is idempotent. Call it from `_init_instrumentors()` **before** official instrumentors if they also patch the same object.

---

## 9. Doc vs code (`FULL_PROJECT_DOCUMENTATION.md`)

| That document says | This repo actually does |
|--------------------|-------------------------|
| `init_tracing` on WSGI import | Only Gunicorn `post_fork` |
| `ViewTracingMiddleware` injected at runtime | Listed in `settings.MIDDLEWARE` |
| `RequestLoggingMiddleware` not traced | It is in `MIDDLEWARE`, so it is wrapped |
| `PostgresSpanProcessor.on_start` sets alias | `on_start` is `pass`; alias in `on_end` |
| Batch 200ms / size 64 | Defaults 2000ms / 256 |

Prefer this file and the source under `observability/` when they disagree.
