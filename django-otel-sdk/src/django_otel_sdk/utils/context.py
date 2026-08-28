import contextvars

_active_db_service_var = contextvars.ContextVar("db_alias", default="postgres")


def set_active_db_context(service_name: str):
    _active_db_service_var.set(service_name)


def get_active_db_context() -> str:
    return _active_db_service_var.get()


def get_service_name_for_connection(connection) -> str:
    connection_alias = getattr(connection, "alias", "default")
    connection_settings = getattr(connection, "settings_dict", {}) or {}
    configured_host = connection_settings.get("HOST")

    if configured_host and configured_host not in ("localhost", "127.0.0.1", ""):
        return configured_host

    if connection_alias == "default":
        return "postgres"
    return f"{connection_alias}db" if not connection_alias.endswith("db") else connection_alias


def extract_cache_resource_namespace(cache_key: str) -> str:
    if not cache_key:
        return ""
    key_string = str(cache_key)
    return key_string.split(":")[0] if ":" in key_string else key_string
