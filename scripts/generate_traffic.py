#!/usr/bin/env python3
"""Traffic Generator Script for Django OpenTelemetry APM PoC.

Simulates realistic multi-service traffic:
- Master DB writes (bulk create, stock adjustments)
- Replica DB reads (slave1, slave2, slave3)
- Redis cache operations (hits and misses)
- External HTTP API calls
- Slow response endpoints
- Error endpoints (500 internal server error)
"""

import argparse
import random
import time
import requests

BASE_URL = "http://localhost:8001"


def run_traffic_loop(duration_seconds=30, delay=0.5):
    start_time = time.time()
    print(f"🚀 Starting APM Traffic Generator for {duration_seconds} seconds...")

    endpoints = [
        # (Method, Path, Weight, Description)
        ("GET", "/api/products/", 10, "List products (Master DB)"),
        ("GET", "/api/products/read-slave1/", 8, "Read from Slave 1 DB"),
        ("GET", "/api/products/read-slave2/", 8, "Read from Slave 2 DB"),
        ("GET", "/api/products/read-slave3/", 8, "Read from Slave 3 DB"),
        ("GET", "/api/products/cache/", 12, "Cache Endpoint (Redis Hit/Miss)"),
        ("GET", "/api/products/external/", 6, "External HTTP Call"),
        ("GET", "/api/products/slow/", 3, "Slow Endpoint (2s delay)"),
        ("GET", "/api/products/error/", 2, "Error Endpoint (500 Error)"),
        ("GET", "/api/products/health/", 5, "Health Check"),
    ]

    total_requests = 0
    errors_encountered = 0

    while time.time() - start_time < duration_seconds:
        # Weighted random choice of endpoint
        weights = [ep[2] for ep in endpoints]
        selected = random.choices(endpoints, weights=weights, k=1)[0]
        method, path, _, desc = selected

        url = f"{BASE_URL}{path}"
        try:
            if method == "GET":
                resp = requests.get(url, timeout=10)
            elif method == "POST":
                resp = requests.post(url, json={"name": f"Product-{random.randint(100, 999)}", "price": "19.99", "stock": 50}, timeout=10)

            total_requests += 1
            status_code = resp.status_code
            print(f"[{time.strftime('%H:%M:%S')}] {method} {path} -> {status_code} ({desc})")
            if status_code >= 400:
                errors_encountered += 1
        except Exception as e:
            total_requests += 1
            errors_encountered += 1
            print(f"[{time.strftime('%H:%M:%S')}] {method} {path} -> FAILED ({e})")

        time.sleep(delay)

    print(f"\n✅ Traffic generation complete. Total: {total_requests} requests, Errors: {errors_encountered}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate live traffic for Django OTel APM")
    parser.add_argument("--duration", type=int, default=30, help="Duration in seconds (default: 30)")
    parser.add_argument("--delay", type=float, default=0.3, help="Delay between requests in seconds (default: 0.3)")
    args = parser.parse_args()

    run_traffic_loop(duration_seconds=args.duration, delay=args.delay)
