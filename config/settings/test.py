import os
import tempfile

# Generate env vars for some things we dont care about in testing
os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-key")
os.environ.setdefault("DJANGO_DATABASE_URL", "sqlite://:memory:")

from .base import *  # noqa: F403

MEDIA_ROOT = tempfile.mkdtemp(prefix="qsology-test-media-")
STORAGES["exports"] = {  # noqa: F405
    "BACKEND": "django.core.files.storage.FileSystemStorage",
    "OPTIONS": {"location": tempfile.mkdtemp(prefix="qsology-test-exports-")},
}
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

# Never wait between geocoder requests in tests
GEOCODER_MIN_INTERVAL = 0

# Hashing passwords properly is slow and tests make a lot of users
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# There's no collected static in tests, don't warn about it on every request
WHITENOISE_AUTOREFRESH = True
