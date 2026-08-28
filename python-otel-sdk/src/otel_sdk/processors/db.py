"""
PostgreSQL span processor — framework-agnostic.

Enriches any span with db.system=postgresql/postgres.  The DB-alias
context helper is optional; if no Django connection is active it falls
back to server.address already on the span.
"""
from opentelemetry.sdk.trace import SpanProcessor

from otel_sdk.utils.context import get_active_db_context
from otel_sdk.utils.query import (
    clean_sql_statement,
    extract_sql_operation,
    format_postgres_span_name,
    parameterize_sql_summary,
)


class PostgresSpanProcessor(SpanProcessor):
    """
    Normalizes postgres spans:
    - postgres.query <summary> as span name
    - db.operation.name / db.query.summary / db.query.text
    - server.address / peer.service via active DB context
    """

    def on_start(self, span, parent_context=None):
        pass

    def on_end(self, span):
        attrs = dict(span.attributes or {})
        db_system = attrs.get("db.system")

        if db_system not in ("postgresql", "postgres"):
            return

        raw_sql = attrs.get("db.statement", "") or attrs.get("db.query.text", "")
        if raw_sql:
            cleaned = clean_sql_statement(raw_sql)
            op = extract_sql_operation(cleaned)
            summary = parameterize_sql_summary(cleaned)
            span._name = format_postgres_span_name(summary)
            if span._attributes is not None:
                span._attributes["db.operation.name"] = op
                span._attributes["db.query.summary"] = summary
                span._attributes["db.query.text"] = raw_sql

        target = get_active_db_context() or attrs.get("server.address") or "postgres"

        if span._attributes is not None:
            span._attributes["server.address"] = str(target)
            span._attributes["peer.service"] = str(target)
            span._attributes["server.port"] = 5432
            span._attributes["db.system"] = "postgresql"
            span._attributes["component"] = "postgresql"
            span._attributes["span.type"] = "db"
            if "db.user" not in attrs:
                span._attributes["db.user"] = "postgres"
