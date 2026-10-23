import pytest
from django.db import connection


@pytest.fixture
def strict_hosts(settings):
    settings.ALLOWED_HOSTS = ["qsology.example"]


class TestLiveness:
    def test_ok(self, client):
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.content == b"ok"
        assert response["Cache-Control"] == "no-store"

    def test_whatever_the_host(self, client, strict_hosts):
        # The kubelet probes the pod by its IP
        response = client.get("/healthz", HTTP_HOST="10.1.2.3:8000")
        assert response.status_code == 200

    def test_doesnt_need_the_database(self, client, monkeypatch):
        def broken(*args, **kwargs):
            raise AssertionError("touched the database")

        monkeypatch.setattr(connection, "ensure_connection", broken)
        assert client.get("/healthz").status_code == 200


@pytest.mark.django_db
class TestReadiness:
    def test_ok(self, client, strict_hosts):
        response = client.get("/readyz", HTTP_HOST="10.1.2.3:8000")
        assert response.status_code == 200

    def test_database_unavailable(self, client, monkeypatch):
        def broken(*args, **kwargs):
            raise ConnectionError("refused")

        monkeypatch.setattr(connection, "ensure_connection", broken)
        response = client.get("/readyz")
        assert response.status_code == 503
        assert response.content == b"database unavailable"


def test_other_paths_still_check_the_host(client, strict_hosts):
    response = client.get("/healthz/", HTTP_HOST="10.1.2.3:8000")
    assert response.status_code == 400
