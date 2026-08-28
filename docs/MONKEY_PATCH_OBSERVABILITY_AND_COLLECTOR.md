# Monkey-Patch, Observability Tracking, and Collector — How It Works Today, Why It's Wrong, and How to Redesign

> Scope: explains the **current** `django-otel-sdk` / `python-otel-sdk` patching, how a request becomes spans/metrics, how the Collector moves data, why the collector exists, and what must change for a proper SDK-level design. File references are to this repo.

---

## 1. What the SDK Patches and Why It Patches

OTel Python has no Django/DB/Redis hooks built in — the app must create or enrich spans. This repo uses **monkey-patching** (replace an attribute at import/runtime with a wrapper that adds span logic, then calls the original).

### 1.1 Patch inventory

| Patch point | File | Original | Wrapper | Flag | Why patch |
|---|---|---|---|---|---|
| `BaseHandler.load_middleware` | `python-otel-sdk/src/otel_sdk/frameworks/django.py:61` | `django.core.handlers.base.BaseHandler.load_middleware` | `load_middleware_traced` which swaps `base.import_string` to return `traced_middleware` closures per middleware class | `._is_otel_traced` on `load_middleware_traced` | Django builds middleware chain once on startup. The only seam to get **one span per middleware `__call__`** without asking users to rewrite middleware is to intercept the loader. |
| `Template.render` | `django.py:125` | `django.template.base.Template.render` | `render_traced` → `with _template_tracer.start_as_current_span("django.template.render <name>")` | `._is_otel_traced` on wrapper | No instrumentor covers template rendering; manual wrap is needed for the `template.name` span. |
| `BaseDatabaseWrapper._cursor` | `django.py:149` | `django.db.backends.base.base.BaseDatabaseWrapper._cursor` | `cursor_traced` → `set_active_db_context(get_service_name_for_connection(self))` then original | `._is_otel_traced` | Need to know **which Django DB alias** (`default`/`slave1`/…) produced the SQL span. `Psycopg2Instrumentor` only sees a psycopg2 connection, not the Django alias. |
| `psycopg2.extensions.cursor.execute` + `executemany` | `django.py:172` | `psycopg2.extensions.cursor.execute` | `execute_traced` → `original then trace.get_current_span().set_attribute("db.row_count", rowcount)` | `._is_otel_traced` | `Psycopg2Instrumentor` does not record `rowcount`. Added here so DB spans carry `db.row_count`. |
| `django_redis.client.DefaultClient.{get,set,delete,get_many,set_many,incr,decr,touch,clear}` | `django.py:212` | Each method on `DefaultClient` | `client_method_wrapper` → explicit `django_redis.cache.<op>` span with `cache.key`, `app.cache.resource`, `db.statement` | `._is_otel_traced` per method | OTel's `RedisInstrumentor` only sees raw `GET`/`SET` commands, not **logical cache ops** with high-cardinality keys. The wrapper gives `app.cache.resource=products` (prefix before `:`) for dashboards. |
| Framework Instrumentors (not a patch, but registration) | `django.py:329` | `DjangoInstrumentor`, `Psycopg2Instrumentor`, `RedisInstrumentor`, `RequestsInstrumentor` | `.instrument()` | Instrumentor-internal `_already_instrumented` | Official OTel wrappers that patch Django request handling, psycopg2, redis-py, and `requests` to create the base spans that the custom processors then enrich. |
| FastAPI/Flask/Starlette | `frameworks/fastapi.py:44`, `flask.py:8`, `starlette.py:9` | `FastAPIInstrumentor.instrument_app`, `FlaskInstrumentor.instrument_app`, `OpenTelemetryMiddleware` | thin `instrument_fastapi/flask/starlette` funcs | instrumentor flags | Same idea — reuse upstream instrumentors, plus an optional `ViewSpanMiddleware` for per-route `view.*` spans. |

### 1.2 Patch mechanics (the pattern)

Every custom patch follows the same 4 steps, visible in `django.py:61-122`:

```python
if getattr(target, "_is_otel_traced", False):
    return                          # 1. idempotency — post_fork may call init twice

original = target                   # 2. save original

def wrapper(*args, **kwargs):
    with tracer.start_as_current_span(name) as span:  # 3. span lifecycle
        span.set_attribute(...)
        try:
            return original(*args, **kwargs)           #    + call original
        except Exception as e:
            span.set_status(ERROR); span.record_exception(e); raise

wrapper._is_otel_traced = True
target = wrapper                    # 4. replace
```

`instrument_middleware` is more subtle (`django.py:73-122`): it patches the **loader** (`load_middleware`), which temporarily patches `base.import_string` so each middleware class is factory-wrapped into `traced_middleware`. The wrapper copies through `sync_capable`/`async_capable` and `process_view`/`process_exception`/`process_template_response` so Django still finds the hooks. It explicitly skips `ViewTracingMiddleware` by import path — otherwise the view span would be double-wrapped.

`django_response_hook` (`django.py:18`) is not a monkey-patch; it's a callback passed to `DjangoInstrumentor().instrument(response_hook=...)` and also by `config/wsgi.py:16`'s `RouteExtractingWSGIMiddleware` which stores `otel.root_span` in `environ` so the hook can rename **both** the Django inner span and the real WSGI `SERVER` span.

### 1.3 Why patching was chosen (and its cost)

* **No framework change required** — users add one `ViewTracingMiddleware` line and one `post_fork` call; everything else is automatic. That mimics Datadog's `ddtrace` auto-instrumentation.
* **Seam for Datadog-like names** — official spans are low-level (`SELECT * FROM ...`); dashboards need `postgres.query <summary>`, `slave2db`, `app.cache.resource`. Processors can only rewrite `on_end`; they need a `ContextVar` set at cursor time to know the alias. Hence the cursor patch.
* Costs: patches touch **private Django/psycopg2 APIs** (`_cursor`, `import_string`), rely on `span._name` / `span._attributes` private mutation in processors (`processors/db.py`, `processors/cache.py`), are sensitive to import order (must run before Django builds the handler), and are fragile across Django/OTel version upgrades. They also mix concerns: `django.py` currently owns DB/Redis alias logic that belongs in the processor layer.

---

## 2. How Observability Tracks a Request (Span Tree)

### 2.1 Bootstrap (before any request)

`gunicorn.conf.py:post_fork` → `init_tracing()` (`sdk.py:150` / `core/tracer.py:28`):

1. `Resource.create({service.name, deployment.environment, application})`
2. `TracerProvider` + 3 processors in order: `PostgresSpanProcessor` → `RedisSpanProcessor` → `BatchSpanProcessor(OTLPSpanExporter -> http://…:4317)`
3. `set_tracer_provider` + `CompositePropagator(W3CTraceContext + Baggage)`
4. `instrument_*` calls (section 1.1) — **custom patches first, then official instrumentors**. Order matters: custom `ContextVar` plumbing must exist before official SQL spans are created.

`config/wsgi.py:16` wraps the app: `OpenTelemetryMiddleware(RouteExtractingWSGIMiddleware(get_wsgi_application()))`. The outer middleware extracts or creates the trace context from `traceparent`/`baggage` headers; the inner one stashes the current `SERVER` span in `environ["otel.root_span"]`.

`worker_exit` → `shutdown_tracer_provider()` flushes the `BatchSpanProcessor` queue so in-flight spans are not lost when Gunicorn recycles the worker.

### 2.2 Per-request span tree

Example `GET /api/products/read-slave2/` (from `OTEL_DJANGO_INSTRUMENTATION.md:99`):

```
SERVER  OpenTelemetryMiddleware  "GET ^api/products/read-slave2/$"  http.route, http.status_code, server.address=django
  ├── django.middleware.security.SecurityMiddleware
  ├── django.middleware.session.SessionMiddleware
  ├── ...
  ├── api.middleware.RequestLoggingMiddleware   (sets request._request_id)
  ├── ViewTracingMiddleware  (NOT wrapped — skipped by path)
  │     process_view: start_span("view.ProductViewSet.read_slave2") + context.attach  ← becomes parent for DB/cache children
  │       ├── postgres.query SELECT …  peer.service=slave2db  db.operation.name=SELECT  db.query.summary  db.row_count
  │       ├── django_redis.cache.get  cache.key=products:all  app.cache.resource=products
  │       └── redis GET  (skipped by RedisSpanProcessor because django_redis.* already covered)
  │     __call__: set http.status_code + end()
  └── django_response_hook: renames SERVER span using resolver_match.route, sets http.route/method/status, copies request.id
          + django.template.render <name>  (if rendering)
          + requests GET -> external-api  (if outbound)
```

Parent-child is **context propagation**: `tracer.start_as_current_span` pushes the span onto `contextvars.Context` (OTel) and `trace.set_span_in_context`. Any nested `start_as_current_span` inside that context becomes a child automatically. `ViewTracingMiddleware.process_view` does manual `start_span` + `context.attach` so its span is current while the view runs, ensuring ORM/Redis spans nest under it rather than directly under the middleware span.

### 2.3 Enrichment (processors, on_end)

*DB path* (`processors/db.py`): `PostgresSpanProcessor.on_end` filters `db.system in (postgresql, postgres)`, reads `db.statement`, cleans/parameterizes it (`utils/query.py`: `clean_sql_statement`, `parameterize_sql_summary`), sets `db.operation.name`, `db.query.summary`, `db.query.text`, rewrites `span._name = "postgres.query <summary>"`, and sets `server.address`/`peer.service` from `get_active_db_context()` (the `ContextVar` set in `cursor_traced`) or from the span's `server.address`. Also forces `db.system=postgresql`, `span.type=db`, `server.port=5432`.

*Cache path* (`processors/cache.py`): `RedisSpanProcessor.on_end` filters `db.system==redis` and skips `django_redis.*` spans, rewrites name to `"<peer> <cmd>"`, sets `server.address=redis`, `peer.service=redis`, `span.type=cache`, `app.cache.operation`.

Why `on_end`? Official instrumentors have already created the span; the processor is the only SDK-level hook that can **rename and add low-cardinality attributes** for all spans without wrapping every call site. It mutates `span._attributes`/`_name` — private API but the standard OTel-contrib pattern.

*Utils*: `utils/context.py` holds `_active_db_service_var: ContextVar("db_alias", default="postgres")` — per-request, per-async-task isolation, unlike thread-locals. `get_service_name_for_connection` maps Django alias → service: `default→postgres`, `slave2→slave2db`, or `HOST` if not localhost. `extract_cache_resource_namespace` splits `products:all_cached` → `products`. `utils/query.py` does whitespace collapse, operation extraction, and `re.sub(r"'\d+'|\b\d+\b|'%s'|%s", "?", sql)` plus truncation to 100 chars.

---

## 3. How the Collector Collects and Why

### 3.1 Data flow (from `otel-collector/config.yaml:82`)

```
[app] BatchSpanProcessor --OTLP gRPC :4317--> [Collector] --OTLP :Tempo + Prometheus--> [Tempo/Prometheus] --Grafana
                                            :4318 (HTTP also)
```

**Collector `service.pipelines` (`config.yaml:89`):**

* `traces`: `receivers:[otlp]` → `processors:[memory_limiter, resource/cleanup, transform, batch]` → `exporters:[otlp/tempo, spanmetrics]`
* `metrics`: `receivers:[spanmetrics]` → `processors:[memory_limiter, batch]` → `exporters:[prometheus]`

**Receivers** (`config.yaml:1`): `otlp` with `grpc 0.0.0.0:4317` and `http 0.0.0.0:4318`. Apps speak OTLP/gRPC via `OTLPSpanExporter(endpoint=…, insecure=True)` (`core/tracer.py:50`). The collector is the **only** ingress; apps never talk to Tempo/Prometheus directly.

**Processors:**

* `memory_limiter` (`config.yaml:36`): `check_interval 5s, limit_percentage 80, spike_limit 25` — drops or throttles when collector approaches OOM; protects the pod without losing data via app backpressure.
* `resource/cleanup` (`config.yaml:40`): deletes `telemetry.sdk.*` attrs — noise for dashboards.
* `transform` (`config.yaml:48`): OTTL statements — `server.address pgbouncer → postgres`, `peer.service` likewise, `replace_pattern(name, "pgbouncer","postgres")`, and backfill `server.address` from `net.peer.name` if missing. Ensures uniform `postgres` service name regardless of pooling.
* `batch` (`config.yaml:32`): `timeout 1s, send_batch_size 512, max 1024` — coalesces per-worker batches into larger OTLP payloads to Tempo, reduces write amplification. Distinct from SDK-side `BatchSpanProcessor` (`batch_delay_millis 2000, queue 2048`).

**Connectors:**

* `spanmetrics` (`config.yaml:10`): synthesizes **RED metrics** (rate/error/duration) from traces without separate instrumentation. `dimensions` list (`http.method`, `http.route`, `server.address`, `peer.service`, `db.system`, `db.operation.name`, `db.query.summary`, `app.cache.operation`, `app.cache.resource`, …) becomes Prometheus label set; `histogram buckets 0.5ms…10s`, `aggregation_temporality cumulative`, `dimensions_cache_size 1000`, `flush 15s`. Grafana service-catalog and endpoint tables read these metrics.

**Exporters:**

* `otlp/tempo` (`config.yaml:59`): `endpoint ${TEMPO_ENDPOINT}`, `insecure`, `sending_queue 100/4`, `retry 5s→30s max 120s` — durable push to Tempo (TraceQL backend).
* `prometheus` (`config.yaml:72`): `endpoint 0.0.0.0:8889` with `resource_to_telemetry_conversion` — exposes Otel metrics in Prometheus exposition format; Prometheus scrapes it.

**Extensions:**

* `health_check` (`config.yaml:78`): `0.0.0.0:13133` — liveness probe for orchestrator.

### 3.2 Why a Collector (not direct export)

Current code **could** point `OTLPSpanExporter` directly at Tempo, but the doc considers that wrong for production:

1. **Decoupling & buffering** — SDK `BatchSpanProcessor` queue is in-process and lost on crash/`KILL`. Collector's `sending_queue` + `retry_on_failure` + `memory_limiter` survives bursts and Tempo downtime; apps remain available even if telemetry backend is.
2. **Single write path** — N Gunicorn workers × M pods would fan out N×M OTLP connections to Tempo. Collector batches (`1s/512`) and multiplexes; Tempo sees one writer.
3. **Transform & normalization** — `pgbouncer→postgres`, `net.peer.name→server.address`, dropping SDK noise. Rules live in one YAML, not in every app's processor.
4. **Metrics from traces** — `spanmetrics` derives Prometheus metrics without a second SDK. Adding a new dashboard dimension requires only `config.yaml:14` plus a span attribute; no app redeploy. Direct-to-Tempo would need a separate metrics pipeline.
5. **Vendor neutrality & sampling** — Collector is where sampling, tail-based sampling, filtering, or tenant routing would be added. Apps stay portable (change endpoint, not code).
6. **Security boundary** — Apps use insecure gRPC inside VPC to collector; collector holds TLS/auth to downstream stores. No secrets in app env.
7. **Ops isolation** — Collector runs as a Deployment/DaemonSet with its own scaling, memory, and observability (`health_check`, `service.telemetry`). App restarts do not reset pipeline state.

Alternative considered: SDK → Tempo (traces) + SDK → Prometheus (metrics) directly. Rejected because it duplicates SDK pipeline, loses the `spanmetrics` guarantee that traces and metrics are consistent (same `http.route`/`db.query.summary` dimensions), and cannot backfill or rewrite attributes after the fact.

---

## 4. Why the Current Design Is Wrong and How to Redesign

The user flagged the design as wrong. The issues are structural, not just missing FastAPI support.

### 4.1 Problems

* **Fragmented patch ownership** — `frameworks/django.py` owns DB alias `ContextVar`, rowcount, and `django_redis` wrapping that belong to the **processor** layer. Django, FastAPI, and Flask each reimplement redis/sql logic instead of sharing a core.
* **Private-API mutation** — `processors/db.py` and `cache.py` mutate `span._name`/`_attributes` — unsupported, breaks with OTel SDK upgrades; also overwrites SDK resource detection order.
* **Loader hack** — `BaseHandler.load_middleware` + `import_string` swap is the most fragile Django private API; it breaks if Django changes the handler initialization, ahead-of-time middleware, or async paths. `instrument_middleware` also re-wraps on every `post_fork` without checking `is_instrumented_by_opentelemetry`.
* **ContextVar misuse** — `_active_db_service_var` is set per cursor but never cleared; concurrent ASGI tasks may leak alias between queries. No scope for non-Django DB drivers (SQLAlchemy, asyncpg).
* **Global provider + fork** — `core/tracer.py:28` calls `set_tracer_provider` per worker; fallback `shutdown_tracer_provider` hacks `_TRACER_PROVIDER_SET_ONCE._done`. The OTel spec allows one provider per process; the new code works around it rather than using the recommended `post_fork` new provider via `TracerProvider(..., shutdown_on_exit)`.
* **Collector config drift** — app processors set `server.address=postgres` while collector `transform` also rewrites `pgbouncer→postgres`; overlapping rewrites hide the true pool topology. Dashboards cannot distinguish pgbouncer queuing from DB time.
* **No sampling, no export pluggability** — SDK hardcodes insecure gRPC, no TLS, no `OTEL_TRACES_SAMPLER`, no `OTEL_PROPAGATORS` override; `OTEL_FRAMEWORKS` is a custom env not in OTel spec.

### 4.2 Target SDK-level design

```
otel_sdk
  core/           Resource, TracerProvider, BatchSpanProcessor, Propagator — no framework imports
  processors/     Pure SpanProcessor (filter on semantic conventions, no Django imports; use OTel Attributes)
  frameworks/     Thin adapters that ONLY register instrumentors and middleware
                  django: adds RequestLoggingMiddleware hook + ViewTracingMiddleware (explicit, not loader hack)
                  fastapi/flask/starlette: instrument_app + optional view span middleware
  instrumentation/ DB/cache: separate packages that set baggage/context, not ContextVar in framework module
  api/            @traced, span(), metrics helpers — stable public surface
```

Rules:

1. **Processors must not import Django** — read `server.address`/`db.system`/`app.cache.*` already on the span; let the client wrapper set the alias via `baggage` or `span.set_attribute` before the official span is created.
2. **Prefer official `instrument()` + middleware** over loader patch — Django 4.2+ `MiddlewareMixin` and `BaseHandler` are public; a real `Middleware` class per layer is testable and does not require `import_string` swap.
3. **Explicit view span** — for new frameworks, use `starlette.middleware.base.BaseHTTPMiddleware` or FastAPI `instrument_app(server_request_hook=…)` to set `http.route` from `route.path_format`, not Django `resolver_match`. Do not patch `Template.render` unless a template engine is detected; guard with `find_spec`.
4. **No `span._*` mutation** — use `SpanProcessor.on_end` that returns a new `SpanData` or use OTel's `SpanExporter` wrapper; if mutation is unavoidable, isolate it behind a single `InternalSpanMutator` with a version-pinned SDK dependency and tests that assert against `ReadableSpan` shape.
5. **Provider per worker** — create the provider in `post_fork` and call `force_flush` + `shutdown` in `worker_exit`; do not attempt to reset `_TRACER_PROVIDER_SET_ONCE` in-process. Tests should spawn subprocesses or use `InMemorySpanExporter` instead of resetting globals.
6. **Collector is the transform layer** — remove app-side `pgbouncer→postgres` rewriting; emit `server.address=pgbouncer` and let the collector's `transform` decide the view. Add a second metric label `pool=pgbouncer` if needed.
7. **OTel-native env** — respect `OTEL_SERVICE_NAME`, `OTEL_TRACES_SAMPLER`, `OTEL_PROPAGATORS`, `OTEL_EXPORTER_OTLP_ENDPOINT`; deprecate custom `OTEL_FRAMEWORKS` in favor of `OTEL_PYTHON_AUTOINSTRUMENTATION` or explicit `frameworks=` kwargs.

Result: `django-otel-sdk` becomes a thin shim (`from otel_sdk.frameworks.django import …; from django_otel_sdk.setup import init_tracing # which injects service_name=django`), and new services pick `python-otel-sdk[fastapi|flask|all]` without Django coupling.

---

## 5. References in this repo

* Boot & env: `gunicorn.conf.py:post_fork`, `config/wsgi.py:16`, `sdk.py:150`, `core/config.py:12`, `core/tracer.py:28`
* Patches: `frameworks/django.py:61,125,149,172,212,329`, `frameworks/fastapi.py:44`, `frameworks/flask.py:8`
* Processors: `processors/db.py:24`, `processors/cache.py:18`, `utils/context.py:3`, `utils/query.py:1`
* Collector: `otel-collector/config.yaml:1-97`, `design.md:116`, `tempo`/`prometheus`/`grafana` dirs

