"""Celery Config"""

import os

from celery.signals import beat_init, worker_process_init

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

app = Celery("qsology")
# All celery settings live in Django settings, prefixed with CELERY_
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@worker_process_init.connect
def _worker_telemetry(**kwargs):
    # In each worker process, exporters' threads don't survive the fork
    from config import telemetry

    telemetry.configure("qsology-worker")


@beat_init.connect
def _beat_telemetry(**kwargs):
    from config import telemetry

    telemetry.configure("qsology-beat")
