import os
from django.core.wsgi import get_wsgi_application
from opentelemetry.instrumentation.wsgi import OpenTelemetryMiddleware
from opentelemetry import trace

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

class RouteExtractingWSGIMiddleware:
    def __init__(self, app):
        self.app = app
    def __call__(self, environ, start_response):
        environ['otel.root_span'] = trace.get_current_span()
        return self.app(environ, start_response)

# Apply OTel WSGI middleware, then our extractor, then Django
application = OpenTelemetryMiddleware(RouteExtractingWSGIMiddleware(get_wsgi_application()))
