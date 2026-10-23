"""Prometheus metrics for requests to the app

Counted by MetricsMiddleware, which works the same whether Django runs
under WSGI or ASGI. In production the metrics are added up across worker
processes and served on their own port, see config.prometheus.

Requests are labelled with the URL pattern they matched (e.g.
"logbook/<int:pk>/"), never the path, so the number of series stays
fixed however many pages there are"""

import time

from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from prometheus_client import Counter, Gauge, Histogram

METHODS = frozenset(
    ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
)
UNMATCHED = "<unmatched>"

REQUESTS = Counter(
    "django_http_requests",
    "Requests handled, by method, URL pattern and status",
    ["method", "route", "status"],
)
DURATION = Histogram(
    "django_http_request_duration_seconds",
    "Time to respond, by method and URL pattern",
    ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)
IN_PROGRESS = Gauge(
    "django_http_requests_in_progress",
    "Requests being handled right now",
    # Summed over the workers that are still alive
    multiprocess_mode="livesum",
)


def _labels(request) -> tuple[str, str]:
    method = request.method if request.method in METHODS else "other"
    match = getattr(request, "resolver_match", None)
    route = match.route if match and match.route else UNMATCHED
    return method, route


def _record(request, started: float, status: int) -> None:
    method, route = _labels(request)
    REQUESTS.labels(method, route, str(status)).inc()
    DURATION.labels(method, route).observe(time.perf_counter() - started)


class MetricsMiddleware:
    """Counts and times every request that gets this far. It goes after
    the health check middleware, so probes aren't counted"""

    sync_capable = True
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        self.is_async = iscoroutinefunction(get_response)
        if self.is_async:
            # Django then awaits what __call__ returns
            markcoroutinefunction(self)

    def __call__(self, request):
        if self.is_async:
            return self._acall(request)
        started = time.perf_counter()
        IN_PROGRESS.inc()
        status = 500
        try:
            response = self.get_response(request)
            status = response.status_code
            return response
        finally:
            IN_PROGRESS.dec()
            _record(request, started, status)

    async def _acall(self, request):
        started = time.perf_counter()
        IN_PROGRESS.inc()
        status = 500
        try:
            response = await self.get_response(request)
            status = response.status_code
            return response
        finally:
            IN_PROGRESS.dec()
            _record(request, started, status)
