"""
Shim — re-exports PostgresSpanProcessor from otel_sdk.processors.db.
"""
try:
    from otel_sdk.processors.db import PostgresSpanProcessor  # noqa: F401
    from otel_sdk.frameworks.django import instrument_django_db_aliases, instrument_psycopg2_rowcount  # noqa: F401
except ImportError:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import SpanProcessor
    from django_otel_sdk.utils.context import get_active_db_context, get_service_name_for_connection, set_active_db_context
    from django_otel_sdk.utils.query import clean_sql_statement, extract_sql_operation, format_postgres_span_name, parameterize_sql_summary

    class PostgresSpanProcessor(SpanProcessor):  # type: ignore[no-redef]
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
                    span._attributes["db.user"] = "django"

    def instrument_django_db_aliases():  # type: ignore[no-redef]
        try:
            from django.db.backends.base.base import BaseDatabaseWrapper
            if getattr(BaseDatabaseWrapper._cursor, "_is_otel_traced", False):
                return
            orig = BaseDatabaseWrapper._cursor

            def cursor_traced(self, *args, **kwargs):
                from django_otel_sdk.utils.context import get_service_name_for_connection as g, set_active_db_context as s
                s(g(self))
                return orig(self, *args, **kwargs)

            cursor_traced._is_otel_traced = True
            BaseDatabaseWrapper._cursor = cursor_traced
        except Exception:
            pass

    def instrument_psycopg2_rowcount():  # type: ignore[no-redef]
        try:
            import psycopg2.extensions
            if getattr(psycopg2.extensions.cursor.execute, "_is_otel_traced", False):
                return
            orig_exec = psycopg2.extensions.cursor.execute
            orig_many = psycopg2.extensions.cursor.executemany

            def execute_traced(self, query, vars=None):
                r = orig_exec(self, query, vars)
                cur = trace.get_current_span()
                if cur and cur.is_recording():
                    rc = getattr(self, "rowcount", -1)
                    cur.set_attribute("db.row_count", rc if (rc is not None and rc >= 0) else -1)
                return r

            def executemany_traced(self, query, vars_list):
                r = orig_many(self, query, vars_list)
                cur = trace.get_current_span()
                if cur and cur.is_recording():
                    rc = getattr(self, "rowcount", -1)
                    cur.set_attribute("db.row_count", rc if (rc is not None and rc >= 0) else -1)
                return r

            execute_traced._is_otel_traced = True
            executemany_traced._is_otel_traced = True
            psycopg2.extensions.cursor.execute = execute_traced
            psycopg2.extensions.cursor.executemany = executemany_traced
        except Exception:
            pass
