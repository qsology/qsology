import asyncio
import uuid

import pytest
from asgiref.sync import iscoroutinefunction
from config.metrics import UNMATCHED, MetricsMiddleware
from django.http import HttpResponse
from django.test import RequestFactory
from prometheus_client import REGISTRY

ROUTE = "exports/<uuid:pk>/download/"


def requests(method, route, status):
    labels = {"method": method, "route": route, "status": str(status)}
    return REGISTRY.get_sample_value("django_http_requests_total", labels) or 0


def observed(method, route):
    labels = {"method": method, "route": route}
    name = "django_http_request_duration_seconds_count"
    return REGISTRY.get_sample_value(name, labels) or 0


def in_progress():
    return REGISTRY.get_sample_value("django_http_requests_in_progress")


@pytest.mark.django_db
class TestThroughDjango:
    def test_counts_by_url_pattern_not_path(self, client):
        url = f"/exports/{uuid.uuid4()}/download/"
        before = observed("GET", ROUTE)
        status = client.get(url).status_code
        after = requests("GET", ROUTE, status)
        assert after >= 1
        assert observed("GET", ROUTE) == before + 1

    def test_unmatched_paths_share_a_label(self, client):
        before = requests("GET", UNMATCHED, 404)
        client.get(f"/nothing-here-{uuid.uuid4()}/")
        assert requests("GET", UNMATCHED, 404) == before + 1

    def test_probes_arent_counted(self, client):
        before = requests("GET", UNMATCHED, 200)
        client.get("/healthz")
        assert requests("GET", UNMATCHED, 200) == before

    def test_odd_methods_are_lumped_together(self, client):
        before = requests("other", UNMATCHED, 404)
        client.generic("PROPFIND", "/nothing-here/")
        assert requests("other", UNMATCHED, 404) == before + 1


class TestMiddleware:
    def test_sync(self):
        middleware = MetricsMiddleware(lambda request: HttpResponse(status=201))
        assert not iscoroutinefunction(middleware)
        before = requests("GET", UNMATCHED, 201)
        response = middleware(RequestFactory().get("/"))
        assert response.status_code == 201
        assert requests("GET", UNMATCHED, 201) == before + 1

    def test_async(self):
        async def view(request):
            assert in_progress() >= 1
            return HttpResponse(status=202)

        middleware = MetricsMiddleware(view)
        assert iscoroutinefunction(middleware)
        before = requests("POST", UNMATCHED, 202)
        response = asyncio.run(middleware(RequestFactory().post("/")))
        assert response.status_code == 202
        assert requests("POST", UNMATCHED, 202) == before + 1

    def test_an_exception_counts_as_a_500(self):
        def view(request):
            raise RuntimeError("boom")

        middleware = MetricsMiddleware(view)
        before = requests("GET", UNMATCHED, 500)
        start = in_progress()
        with pytest.raises(RuntimeError):
            middleware(RequestFactory().get("/"))
        assert requests("GET", UNMATCHED, 500) == before + 1
        assert in_progress() == start
