import threading
import time

import requests
from django.core.cache import cache
from django.db import connection, transaction
from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Product
from .serializers import ProductSerializer


class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer

    @action(detail=False, methods=["get"], url_path="read-slave1")
    def read_slave1(self, request):
        products = Product.objects.using("slave1").all()
        serializer = self.get_serializer(products, many=True)
        return Response({
            "database": "slave1",
            "count": len(serializer.data),
            "products": serializer.data,
        })

    @action(detail=False, methods=["get"], url_path="read-slave2")
    def read_slave2(self, request):
        products = Product.objects.using("slave2").all()
        serializer = self.get_serializer(products, many=True)
        return Response({
            "database": "slave2",
            "count": len(serializer.data),
            "products": serializer.data,
        })

    @action(detail=False, methods=["get"], url_path="read-slave3")
    def read_slave3(self, request):
        products = Product.objects.using("slave3").all()
        serializer = self.get_serializer(products, many=True)
        return Response({
            "database": "slave3",
            "count": len(serializer.data),
            "products": serializer.data,
        })

    @action(detail=False, methods=["get"], url_path="cache")
    def cache_endpoint(self, request):
        cache_key = "products:all_cached"
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            return Response({"cache_hit": True, "source": "redis_cache", "products": cached_data})

        products = Product.objects.using("default").all()
        serializer = self.get_serializer(products, many=True)
        cache.set(cache_key, serializer.data, timeout=300)
        return Response({"cache_hit": False, "source": "database", "products": serializer.data})

    @action(detail=False, methods=["get"], url_path="external")
    def external_endpoint(self, request):
        from django.conf import settings
        target_url = request.query_params.get("url", getattr(settings, "EXTERNAL_API_URL", "http://external-api:8080/get"))
        timeout = int(request.query_params.get("timeout", 5))
        try:
            resp = requests.get(target_url, timeout=timeout)
            return Response({
                "status_code": resp.status_code,
                "target_url": target_url,
                "response_time_ms": resp.elapsed.total_seconds() * 1000,
                "data": resp.text[:500],
            })
        except requests.RequestException as e:
            return Response({"error": str(e), "target_url": target_url}, status=status.HTTP_502_BAD_GATEWAY)

    @action(detail=False, methods=["get"], url_path="error")
    def error_endpoint(self, request):
        raise RuntimeError("Deliberate server error for OTel testing")

    @action(detail=False, methods=["get"], url_path="slow")
    def slow_endpoint(self, request):
        time.sleep(2)
        products = Product.objects.using("default").all()[:5]
        serializer = self.get_serializer(products, many=True)
        return Response({"message": "Slow endpoint response (2s delay)", "products": serializer.data})

    @action(detail=True, methods=["get"])
    def cached(self, request, pk=None):
        cache_key = f"product:{pk}"

        cached_product = cache.get(cache_key)
        if cached_product is not None:
            return Response({"cache_hit": True, "source": "cache", "product": cached_product})

        try:
            product = self.get_object()
        except Product.DoesNotExist:
            return Response(
                {"error": "Product not found"}, status=status.HTTP_404_NOT_FOUND
            )

        serializer = self.get_serializer(product)
        cache.set(cache_key, serializer.data, timeout=300)

        return Response({"cache_hit": False, "source": "database", "product": serializer.data})

    @action(detail=True, methods=["get"])
    def slow(self, request, pk=None):
        time.sleep(2)
        try:
            product = self.get_object()
        except Product.DoesNotExist:
            return Response(
                {"error": "Product not found"}, status=status.HTTP_404_NOT_FOUND
            )
        serializer = self.get_serializer(product)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def error(self, request, pk=None):
        raise RuntimeError("Deliberate server error for OTel testing")

    @action(detail=False, methods=["get"])
    def health(self, request):
        checks = {}

        try:
            Product.objects.using("default").first()
            checks["postgres_default"] = "ok"
        except Exception as e:
            checks["postgres_default"] = f"error: {e}"

        for slave in ["slave1", "slave2", "slave3"]:
            try:
                Product.objects.using(slave).first()
                checks[f"postgres_{slave}"] = "ok"
            except Exception as e:
                checks[f"postgres_{slave}"] = f"error: {e}"

        try:
            cache.set("_health_check", "ok", timeout=5)
            val = cache.get("_health_check")
            checks["redis"] = "ok" if val == "ok" else "error: value mismatch"
        except Exception as e:
            checks["redis"] = f"error: {e}"

        healthy = all(v == "ok" for v in checks.values())
        return Response(
            {"status": "healthy" if healthy else "degraded", "checks": checks},
            status=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @action(detail=False, methods=["post"])
    def bulk_create(self, request):
        products_data = request.data if isinstance(request.data, list) else [request.data]
        created = []
        with transaction.atomic():
            for data in products_data:
                serializer = self.get_serializer(data=data)
                serializer.is_valid(raise_exception=True)
                product = serializer.save()
                created.append(product)
        return Response(ProductSerializer(created, many=True).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def adjust_stock(self, request, pk=None):
        product = self.get_object()
        delta = request.data.get("delta", 0)
        with transaction.atomic():
            product = Product.objects.select_for_update().get(pk=product.pk)
            product.stock = max(0, product.stock + delta)
            product.save()
        return Response(ProductSerializer(product).data)


class ExternalCallView(APIView):
    def get(self, request):
        url = request.query_params.get("url", "https://httpbin.org/get")
        timeout = int(request.query_params.get("timeout", 5))
        try:
            resp = requests.get(url, timeout=timeout)
            return Response({
                "status_code": resp.status_code,
                "url": url,
                "response_time_ms": resp.elapsed.total_seconds() * 1000,
            })
        except requests.RequestException as e:
            return Response({"error": str(e), "url": url}, status=status.HTTP_502_BAD_GATEWAY)


class DBTransactionView(APIView):
    def post(self, request):
        operations = request.data.get("operations", 5)
        results = []
        with transaction.atomic():
            for i in range(operations):
                product = Product.objects.create(
                    name=f"Tx Product {i}",
                    description=f"From transaction {i}",
                    price="10.00",
                    stock=i,
                )
                results.append(product.id)
                Product.objects.filter(pk=product.pk).update(description=f"Updated {i}")
        return Response({"created_ids": results, "count": len(results)})


class ThreadedView(APIView):
    def get(self, request):
        num_threads = int(request.query_params.get("threads", 3))
        delay = float(request.query_params.get("delay", 0.5))
        results = []

        def worker(idx):
            time.sleep(delay)
            results.append({"thread": idx, "done": True})

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        return Response({"threads": num_threads, "results": results})


class RawSQLView(APIView):
    def get(self, request):
        query = request.query_params.get("query", "SELECT 1 as test")
        with connection.cursor() as cursor:
            cursor.execute(query)
            columns = [col[0] for col in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return Response({"query": query, "rows": rows, "row_count": len(rows)})


class ManualSpanView(APIView):
    def get(self, request):
        try:
            from opentelemetry import trace
            tracer = trace.get_tracer(__name__)
        except ImportError:
            return Response({"error": "opentelemetry not installed"}, status=status.HTTP_501_NOT_IMPLEMENTED)

        with tracer.start_as_current_span("manual-span") as span:
            span.set_attribute("custom.attribute", "test-value")
            with tracer.start_as_current_span("child-span") as child:
                child.set_attribute("child.data", "nested")
                time.sleep(0.1)
            time.sleep(0.1)
        return Response({"message": "Manual spans created"})


class ProductTemplateView(TemplateView):
    template_name = "products/list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["products"] = Product.objects.all()[:20]
        return context


class ProductDetailTemplateView(TemplateView):
    template_name = "products/detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pk = kwargs.get("pk")
        try:
            context["product"] = Product.objects.get(pk=pk)
        except Product.DoesNotExist:
            context["product"] = None
        return context


@api_view(["GET"])
def metrics_summary(request):
    from django.db.models import Count, Sum, Avg
    stats = Product.objects.aggregate(
        total=Count("id"),
        total_stock=Sum("stock"),
        avg_price=Avg("price"),
    )
    return Response(stats)


@api_view(["GET"])
def db_info(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT version()")
        version = cursor.fetchone()[0]
        cursor.execute("SELECT current_database(), current_user, inet_server_addr(), inet_server_port()")
        db, user, addr, port = cursor.fetchone()
    return Response({
        "version": version,
        "database": db,
        "user": user,
        "host": addr,
        "port": port,
    })


@csrf_exempt
def webhook_receiver(request):
    if request.method == "POST":
        import json
        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            payload = {}
        return JsonResponse({"received": True, "payload": payload})
    return JsonResponse({"error": "POST only"}, status=405)


@api_view(["GET"])
def cache_stats(request):
    return Response({
        "backend": cache.__class__.__name__,
        "keys_sample": list(cache.keys("*")[:10]) if hasattr(cache, "keys") else "N/A",
    })


class DashboardView(TemplateView):
    template_name = "dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        endpoints = [
            {"name": "POST Create Product", "url": "/api/products/", "method": "POST", "description": "Create product on default postgres DB", "params": [], "body": '{"name": "New Product", "description": "Created via API", "price": "29.99", "stock": 50}', "category": "Frozen Topology Endpoints"},
            {"name": "Read Slave 1", "url": "/api/products/read-slave1/", "method": "GET", "description": "Query products explicitly from slave1db database", "params": [], "category": "Frozen Topology Endpoints"},
            {"name": "Read Slave 2", "url": "/api/products/read-slave2/", "method": "GET", "description": "Query products explicitly from slave2db database", "params": [], "category": "Frozen Topology Endpoints"},
            {"name": "Read Slave 3", "url": "/api/products/read-slave3/", "method": "GET", "description": "Query products explicitly from slave3db database", "params": [], "category": "Frozen Topology Endpoints"},
            {"name": "Cache (Redis)", "url": "/api/products/cache/", "method": "GET", "description": "Exercise Redis cache get/set logic", "params": [], "category": "Frozen Topology Endpoints"},
            {"name": "External HTTP Call", "url": "/api/products/external/", "method": "GET", "description": "Perform HTTP call to external-api container", "params": [], "category": "Frozen Topology Endpoints"},
            {"name": "Server Error (500)", "url": "/api/products/error/", "method": "GET", "description": "Deliberate RuntimeError for error tracing", "params": [], "category": "Frozen Topology Endpoints"},
            {"name": "Slow Request (2s)", "url": "/api/products/slow/", "method": "GET", "description": "Artificial 2s latency delay", "params": [], "category": "Frozen Topology Endpoints"},
            {"name": "Product List", "url": "/api/products/", "method": "GET", "description": "List all products on default DB", "params": [], "category": "Products"},
            {"name": "Product Detail (DB)", "url": "/api/products/1/", "method": "GET", "description": "Get single product from default DB", "params": [], "category": "Products"},
            {"name": "Health Check", "url": "/api/products/health/", "method": "GET", "description": "Health check for all 4 PostgreSQL instances and Redis", "params": [], "category": "Products"},
            {"name": "Bulk Create", "url": "/api/products/bulk_create/", "method": "POST", "description": "Create multiple products in transaction", "params": [], "body": '[{"name": "A", "price": "1.00", "stock": 1}, {"name": "B", "price": "2.00", "stock": 2}]', "category": "Products"},
            {"name": "Adjust Stock", "url": "/api/products/1/adjust_stock/", "method": "POST", "description": "Atomic stock adjustment with select_for_update", "params": [], "body": '{"delta": -1}', "category": "Products"},
            {"name": "DB Transaction", "url": "/api/db-tx/", "method": "POST", "description": "Multi-statement database transaction", "params": [], "body": '{"operations": 3}', "category": "Tracing / OTel"},
            {"name": "Threaded Work", "url": "/api/threaded/", "method": "GET", "description": "Run work in parallel threads", "params": [{"name": "threads", "default": "3", "desc": "Number of threads"}, {"name": "delay", "default": "0.5", "desc": "Delay per thread (seconds)"}], "category": "Tracing / OTel"},
            {"name": "Raw SQL", "url": "/api/raw-sql/", "method": "GET", "description": "Execute raw SQL query", "params": [{"name": "query", "default": "SELECT 1 as test", "desc": "SQL query to execute"}], "category": "Tracing / OTel"},
            {"name": "Manual OTel Span", "url": "/api/manual-span/", "method": "GET", "description": "Create manual OpenTelemetry spans", "params": [], "category": "Tracing / OTel"},
            {"name": "Metrics Summary", "url": "/api/metrics/", "method": "GET", "description": "Aggregated product statistics", "params": [], "category": "Debug / Info"},
            {"name": "DB Info", "url": "/api/db-info/", "method": "GET", "description": "PostgreSQL connection information", "params": [], "category": "Debug / Info"},
            {"name": "Webhook Receiver", "url": "/api/webhook/", "method": "POST", "description": "Receive webhook payloads", "params": [], "body": '{"event": "test", "data": {"key": "value"}}', "category": "Debug / Info"},
            {"name": "Cache Stats", "url": "/api/cache-stats/", "method": "GET", "description": "Redis cache backend info", "params": [], "category": "Debug / Info"},
            {"name": "Product List (HTML)", "url": "/api/products-tmpl/", "method": "GET", "description": "HTML product list page", "params": [], "category": "UI"},
            {"name": "Admin", "url": "/admin/", "method": "GET", "description": "Django Admin (admin/admin)", "params": [], "category": "UI"},
        ]

        categories = {}
        for ep in endpoints:
            cat = ep["category"]
            categories.setdefault(cat, []).append(ep)

        context["categories"] = categories
        return context
