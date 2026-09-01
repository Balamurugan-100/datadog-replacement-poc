"""Template span — wraps django.template.base.Template.render."""
from typing import Any, Callable

from opentelemetry.trace import SpanKind, StatusCode, get_tracer


def traced_template_render(wrapped: Callable, instance: Any, args: Any, kwargs: Any) -> Any:
    template_name = getattr(instance, "name", None) or getattr(instance, "origin", None)
    if template_name is not None and hasattr(template_name, "name"):
        try:
            template_name = template_name.name  # type: ignore
        except Exception:
            pass
    template_str = str(template_name) if template_name else ""

    if not template_str or template_str.startswith("debug_toolbar/") or template_str.startswith("django/forms/widgets/"):
        return wrapped(*args, **kwargs)

    tracer = get_tracer("tp_obs_v3.django")
    engine = getattr(instance, "engine", None)
    span_name = f"django.template: {template_str}"
    span_attrs = {"django.template.name": template_str}
    if engine:
        span_attrs["django.template.engine.class"] = engine.__class__.__name__

    with tracer.start_as_current_span(span_name, kind=SpanKind.INTERNAL, attributes=span_attrs) as span:
        try:
            result = wrapped(*args, **kwargs)
            span.set_status(StatusCode.OK)
            return result
        except Exception as exc:
            span.record_exception(exc)
            span.set_attribute("error.type", exc.__class__.__name__)
            span.set_status(StatusCode.ERROR, description=str(exc))
            raise
