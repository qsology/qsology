"""Celery Config"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

app = Celery("qsology")
# All celery settings live in Django settings, prefixed with CELERY_
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
