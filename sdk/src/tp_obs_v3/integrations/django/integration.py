"""DjangoIntegration — orchestrates request/middleware/view/template patches."""
import importlib
import logging
from typing import Any

from tp_obs_v3.integrations.base import BaseIntegration
from tp_obs_v3.integrations.django.metrics import reset_metrics

logger = logging.getLogger("tp_obs_v3.integrations.django")


class DjangoIntegration(BaseIntegration):
    """Full waterfall instrumentation for Django applications."""

    name = "django"

    def __init__(self) -> None:
        super().__init__()
        # Instance set — not global — so multiple DjangoIntegration instances
        # in same process (e.g. tests) don't leak state. Cleared on uninstrument.
        self._wrapped_middleware: set[str] = set()

    def is_installed(self) -> bool:
        try:
            importlib.import_module("django")
            return True
        except ImportError:
            return False

    def _apply_patch(self) -> None:
        try:
            import django  # noqa: F401
            from django.core.handlers.base import BaseHandler
            from django.template.base import Template
        except ImportError:
            logger.debug("Django is not installed, skipping patch.")
            return

        # Import from submodules (keeps this file small, follows requested layout)
        from .middleware import make_traced_load_middleware
        from .request import traced_get_response
        from .template import traced_template_render
        from .view import traced_get_response as traced_view

        # 0. Middleware — must be first so later wrappers see instrumented chain
        self.wrap(
            "django.core.handlers.base.BaseHandler",
            "load_middleware",
            make_traced_load_middleware(self),
        )

        # 1. Request (SERVER) + metrics
        self.wrap("django.core.handlers.base.BaseHandler", "get_response", traced_get_response)

        # 2. View (INTERNAL)
        self.wrap("django.core.handlers.base.BaseHandler", "_get_response", traced_view)

        # 3. Template
        self.wrap("django.template.base.Template", "render", traced_template_render)

        # 4. Management commands (deprioritized, keep if simple)
        try:
            from django.core.management.base import BaseCommand

            from opentelemetry.trace import SpanKind, StatusCode, get_tracer

            def _traced_command(wrapped, instance, args, kwargs):
                tracer = get_tracer("tp_obs_v3.django")
                cmd_name = instance.__class__.__module__.split(".")[-1]
                span_name = f"django.command.{cmd_name}"
                attrs = {"django.command.name": cmd_name, "django.command.class": instance.__class__.__name__}
                with tracer.start_as_current_span(span_name, kind=SpanKind.SERVER, attributes=attrs) as span:
                    try:
                        res = wrapped(*args, **kwargs)
                        span.set_status(StatusCode.OK)
                        return res
                    except Exception as exc:
                        span.record_exception(exc)
                        span.set_attribute("error.type", exc.__class__.__name__)
                        span.set_status(StatusCode.ERROR, description=str(exc))
                        raise

            self.wrap("django.core.management.base.BaseCommand", "execute", _traced_command)
        except Exception as exc:
            logger.debug("Django command instrumentation skipped: %s", exc)

    def _remove_patch(self) -> None:
        try:
            from django.core.handlers.base import BaseHandler

            if hasattr(BaseHandler, "_tp_middleware_instrumented"):
                try:
                    delattr(BaseHandler, "_tp_middleware_instrumented")
                except Exception:
                    pass
        except Exception:
            pass
        self._wrapped_middleware.clear()
        reset_metrics()
        super()._remove_patch()
