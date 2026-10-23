"""Health checks for Kubernetes

/healthz says the process is alive and serving, /readyz that it can reach
the database too, so a pod that can't is taken out of the load balancer
rather than restarted. Answered before the rest of the middleware, so they
work whatever Host the kubelet sends and never touch sessions"""

import logging

from django.db import connection
from django.http import HttpResponse

logger = logging.getLogger(__name__)

LIVE = "/healthz"
READY = "/readyz"


def _ready() -> bool:
    try:
        connection.ensure_connection()
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        logger.warning(
            "Not ready, the database can't be reached", exc_info=True
        )
        return False
    return True


class HealthCheckMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == LIVE:
            return _plain("ok")
        if request.path == READY:
            if _ready():
                return _plain("ok")
            return _plain("database unavailable", status=503)
        return self.get_response(request)


def _plain(text: str, status: int = 200) -> HttpResponse:
    response = HttpResponse(text, status=status, content_type="text/plain")
    response["Cache-Control"] = "no-store"
    return response
