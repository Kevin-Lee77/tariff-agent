from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

HTTP_REQUESTS = Counter("tariff_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram("tariff_http_request_duration_seconds", "HTTP latency", ["method", "route"])


def metrics_response():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
