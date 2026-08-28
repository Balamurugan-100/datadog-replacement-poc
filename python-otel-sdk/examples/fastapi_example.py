"""
FastAPI example — shows how the generic SDK replaces the Django-only SDK.

Run:
    pip install -e "./python-otel-sdk[fastapi]"
    OTEL_SERVICE_NAME=fastapi-demo OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 uvicorn examples.fastapi_example:app --reload

Traces appear in Grafana Tempo the same as Django traces — same OTLP collector.
"""
from fastapi import FastAPI
from otel_sdk import init_tracing, shutdown_tracing, span, traced

# --- SDK init (one line, framework-agnostic) ---
app = FastAPI(title="FastAPI OTEL Demo")

# Option 1: pass app directly
init_tracing(service_name="fastapi-demo", frameworks=["fastapi"], app=app)

# Option 2 (equivalent, two-step):
# from otel_sdk import init_tracing
# from otel_sdk.frameworks.fastapi import instrument_fastapi
# init_tracing(service_name="fastapi-demo")
# instrument_fastapi(app)


@traced
def _heavy_business_logic(order_id: int) -> dict:
    with span("db.query", attributes={"db.operation": "SELECT"}):
        # simulate DB call — in real code your asyncpg/sqlalchemy spans
        # will be auto-created and enriched by PostgresSpanProcessor
        pass
    return {"order_id": order_id, "status": "processed"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/orders/{order_id}")
def get_order(order_id: int):
    with span("orders.fetch", attributes={"order.id": order_id}):
        result = _heavy_business_logic(order_id)
    return result


@app.get("/orders")
async def list_orders():
    # async handlers also work with @traced
    return [{"order_id": i} for i in range(3)]


# Graceful shutdown — flush spans (also for gunicorn worker_exit)
@app.on_event("shutdown")
def on_shutdown():
    shutdown_tracing()
