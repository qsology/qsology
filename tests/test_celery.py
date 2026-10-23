"""Celery is set up for a RabbitMQ cluster, see CELERY_ in settings"""

import importlib.util
import ssl

from config.celery import app


class TestBroker:
    def test_queues_are_replicated(self):
        queue = app.amqp.queues[app.conf.task_default_queue]
        assert queue.queue_arguments == {"x-queue-type": "quorum"}
        assert app.conf.task_create_missing_queue_type == "quorum"

    def test_delayed_tasks_wait_in_rabbitmq(self):
        # Native delayed delivery only works through a topic exchange
        queue = app.amqp.queues[app.conf.task_default_queue]
        assert queue.exchange.type == "topic"

    def test_tasks_are_confirmed_before_sending_returns(self):
        assert app.conf.broker_transport_options["confirm_publish"] is True

    def test_no_transient_shared_queues(self):
        # Deprecated in RabbitMQ 4
        assert app.conf.control_queue_exclusive
        assert app.conf.event_queue_exclusive


class TestBrokerTLS:
    """Settings are read once, so these load base.py again with the
    environment they need"""

    def settings_with(self, monkeypatch, **environ):
        for name, value in environ.items():
            monkeypatch.setenv(name, value)
        spec = importlib.util.find_spec("config.settings.base")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_checks_the_certificate_and_hostname(self, monkeypatch):
        settings = self.settings_with(
            monkeypatch,
            CELERY_BROKER_URL="amqps://u:p@rabbitmq.example:5671/qsology",
            CELERY_BROKER_CA_FILE="/etc/qsology/db-ca/ca.crt",
        )
        assert settings.CELERY_BROKER_USE_SSL == {
            "cert_reqs": ssl.CERT_REQUIRED,
            "ca_certs": "/etc/qsology/db-ca/ca.crt",
            # kombu uses the URL's host
            "server_hostname": None,
        }

    def test_system_cas_without_a_ca_file(self, monkeypatch):
        monkeypatch.delenv("CELERY_BROKER_CA_FILE", raising=False)
        settings = self.settings_with(
            monkeypatch, CELERY_BROKER_URL="amqps://u:p@rabbitmq.example/"
        )
        assert settings.CELERY_BROKER_USE_SSL["ca_certs"] is None

    def test_plain_amqp_has_no_tls(self, monkeypatch):
        settings = self.settings_with(
            monkeypatch, CELERY_BROKER_URL="amqp://guest:guest@localhost//"
        )
        assert not hasattr(settings, "CELERY_BROKER_USE_SSL")
