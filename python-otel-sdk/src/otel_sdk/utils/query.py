import re


def clean_sql_statement(raw_sql: str) -> str:
    if not raw_sql:
        return ""
    return " ".join(raw_sql.strip().split())


def extract_sql_operation(cleaned_sql: str) -> str:
    if not cleaned_sql:
        return "QUERY"
    return cleaned_sql.split()[0].upper()


def parameterize_sql_summary(cleaned_sql: str, max_length: int = 100) -> str:
    if not cleaned_sql:
        return ""
    parameterized = re.sub(r"'\d+'|\b\d+\b|'%s'|%s", "?", cleaned_sql)
    if len(parameterized) > max_length:
        return parameterized[: max_length - 3] + "..."
    return parameterized


def format_postgres_span_name(query_summary: str) -> str:
    return f"postgres.query {query_summary}"
