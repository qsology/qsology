import os
import tempfile

# Generate env vars for some things we dont care about in testing
os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-key")
os.environ.setdefault("DJANGO_DATABASE_URL", "sqlite://:memory:")

from .base import *  # noqa: F403

MEDIA_ROOT = tempfile.mkdtemp(prefix="qsology-test-media-")
DEBUG = False
ALLOWED_HOSTS = ["testserver"]

MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    },
}

# Run celery tasks inline, no broker needed
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"
