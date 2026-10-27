import ssl
from datetime import timedelta
from pathlib import Path

from django.utils.csp import CSP
from environs import Env

from config import cache

# Django base dir
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Environment setup
env = Env()
# Read in .env, should also fall back to env vars
env.read_env(str(BASE_DIR / ".env"), recurse=False)

# Secret Key
SECRET_KEY = env.str("DJANGO_SECRET_KEY")
# Debug - Default to False
DEBUG = env.bool("DJANGO_DEBUG", default=False)
# Allowed hosts
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost"])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])
# Root URL Paterns
ROOT_URLCONF = "config.urls"
# SGI's
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Templates
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [str(BASE_DIR / "templates")],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.csp",
                "apps.web.context_processors.preferences",
            ],
        },
    },
]

# Language Code - default to English (US)
LANGUAGE_CODE = "en-us"
USE_I18N = True
# There is no other timezone, only UTC
TIME_ZONE = "UTC"
USE_TZ = True


# Static URLS and paths
STATIC_URL = "static/"
STATICFILES_DIRS = [str(BASE_DIR / "static")]
STATIC_ROOT = str(BASE_DIR / "staticfiles")
MEDIA_ROOT = env.path("DJANGO_MEDIA_ROOT", default=str(BASE_DIR / "media"))
MEDIA_URL = "/media/"

# Custom user model
AUTH_USER_MODEL = "accounts.User"

# Site IDs may be used in the future, defaulting to 1
SITE_ID = env.int("DJANGO_SITE_ID", default=1)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Middleware
MIDDLEWARE = [
    # First, so probes are answered whatever Host they're sent with
    "config.health.HealthCheckMiddleware",
    # Next, so it counts everything but the probes
    "config.metrics.MetricsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    # Pages compressed for slow connections. Django masks the CSRF token
    # differently in every response, which is what BREACH needs to be
    # the same
    "django.middleware.gzip.GZipMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# Authenication will be moved to all auth in the future
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
]

FORM_RENDERER = "apps.web.forms.Renderer"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "web:home"
LOGOUT_REDIRECT_URL = "web:home"
# A form sent with a stale CSRF token (after signing in again in another
# tab, say) is shown again to send, rather than lost
CSRF_FAILURE_VIEW = "apps.web.views.csrf_failure"

# Only the app's own files: no inline scripts or styles, nothing from
# elsewhere. See docs/web.md
SECURE_CSP = {
    "default-src": [CSP.SELF],
    "script-src": [CSP.SELF],
    "style-src": [CSP.SELF],
    "img-src": [CSP.SELF, "data:"],
    "connect-src": [CSP.SELF],
    "font-src": [CSP.SELF],
    "form-action": [CSP.SELF],
    "frame-ancestors": [CSP.NONE],
    "base-uri": [CSP.NONE],
    "object-src": [CSP.NONE],
}

# Imports are read in the request, this keeps one from tying up a worker
# for long. ADIF is about 300 bytes a QSO, so this is ~65,000 QSOs
IMPORT_MAX_BYTES = env.int("QSOLOGY_IMPORT_MAX_BYTES", default=20 * 1024 * 1024)

# Storages config
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
# Databases
DATABASES = {
    "default": env.dj_db_url(
        "DJANGO_DATABASE_URL",
        conn_max_age=env.int("DJANGO_DATABASE_CONN_MAX_AGE", default=0),
        conn_health_checks=True,
    ),
}

# Cache, e.g. redis://user:password@valkey:6379/1, in memory if not given
CACHES = {
    "default": cache.from_url(env.str("DJANGO_CACHE_URL", default="locmem://")),
}

# Apps
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    # Its widget templates, for the form renderer (FORM_RENDERER)
    "django.forms",
]

THIRD_PARTY_APPS = []

LOCAL_APPS = [
    "apps.web",
    "apps.accounts",
    "apps.callsigns",
    "apps.locations",
    "apps.qrz",
    "apps.logbook",
    "apps.notifications",
    "apps.audit",
]
INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# Email is taken from the URL in the environment (smtp://u:p@host:port/?tls=true"
_email = env.dj_email_url("DJANGO_EMAIL_URL", default="console://")
if _email["EMAIL_BACKEND"] == "django.core.mail.backends.smtp.EmailBackend":
    _mailer_options = {
        "host": _email["EMAIL_HOST"],
        "port": _email["EMAIL_PORT"],
        "use_tls": _email["EMAIL_USE_TLS"],
        "use_ssl": _email["EMAIL_USE_SSL"],
        "username": _email["EMAIL_HOST_USER"],
        "password": _email["EMAIL_HOST_PASSWORD"],
        "timeout": _email["EMAIL_TIMEOUT"],
    }
else:
    _mailer_options = {}

MAILERS = {
    "default": {
        "BACKEND": _email["EMAIL_BACKEND"],
        "OPTIONS": _mailer_options,
    }
}

DEFAULT_FROM_EMAIL = env.str(
    "DJANGO_DEFAULT_FROM_EMAIL", default="qsology@localhost"
)
SERVER_EMAIL = env.str("DJANGO_SERVER_EMAIL", default=DEFAULT_FROM_EMAIL)


# How long a user has to change their mind after requesting account deletion
ACCOUNT_DELETION_GRACE_PERIOD = timedelta(
    days=env.int("DJANGO_ACCOUNT_DELETION_GRACE_DAYS", default=30)
)

# Celery, with a RabbitMQ cluster as the broker. Point it at an address
# that only leads to nodes that are up (a Kubernetes Service, a load
# balancer), and reconnecting after losing a node lands on another.
# Several URLs separated by ; also work, but celery only moves past one
# that's down for its main connection: remote control and the worker's
# start-up reach for the first URL and give up if it times out
CELERY_BROKER_URL = env.str(
    "CELERY_BROKER_URL", default="amqp://guest:guest@localhost:5672//"
)
# TLS to the broker: check its certificate against CELERY_BROKER_CA_FILE
# (the system's CAs if unset) and its hostname. server_hostname=None makes
# kombu use the URL's host. Only for amqps://, so local RabbitMQ stays plain
if CELERY_BROKER_URL.startswith("amqps://"):
    CELERY_BROKER_USE_SSL = {
        "cert_reqs": ssl.CERT_REQUIRED,
        "ca_certs": env.str("CELERY_BROKER_CA_FILE", default=None),
        "server_hostname": None,
    }
# Quorum queues are replicated across the cluster, so a queued task
# survives losing a node (a majority of nodes has to be up). Delayed tasks
# (countdown, eta, retries) use RabbitMQ's native delayed delivery, which
# celery sets up when it sees quorum queues. It needs a topic exchange,
# otherwise the worker holds a delayed task in memory until it's due
CELERY_TASK_DEFAULT_QUEUE_TYPE = "quorum"
CELERY_TASK_CREATE_MISSING_QUEUE_TYPE = "quorum"
CELERY_TASK_DEFAULT_EXCHANGE_TYPE = "topic"
# A task isn't sent until the cluster has confirmed it has it
CELERY_BROKER_TRANSPORT_OPTIONS = {"confirm_publish": True}
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
# Workers take one task at a time, so a long export doesn't hold back
# tasks another worker could be running
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
# RabbitMQ 4 deprecates transient queues that aren't exclusive, which is
# what celery's remote control and event queues are by default
CELERY_CONTROL_QUEUE_EXCLUSIVE = True
CELERY_EVENT_QUEUE_EXCLUSIVE = True
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
# Task events, which celery-exporter turns into metrics
CELERY_WORKER_SEND_TASK_EVENTS = True
CELERY_TASK_SEND_SENT_EVENT = True
CELERY_BEAT_SCHEDULE = {
    "delete-scheduled-users": {
        "task": "apps.accounts.tasks.delete_scheduled_users",
        "schedule": timedelta(hours=24),
    },
    "refresh-references": {
        "task": "apps.locations.tasks.refresh_references",
        "schedule": timedelta(days=7),
    },
    "tidy-exports": {
        "task": "apps.logbook.tasks.tidy_exports",
        "schedule": timedelta(minutes=15),
    },
    "tidy-task-runs": {
        "task": "apps.audit.tasks.tidy_task_runs",
        "schedule": timedelta(hours=24),
    },
}
# How long the record of each background task run is kept, see apps.audit
TASK_AUDIT_RETENTION = timedelta(
    days=env.int("QSOLOGY_TASK_AUDIT_RETENTION_DAYS", 365)
)

# Outbound HTTP, external services ask that we identify ourselves
HTTP_USER_AGENT = env.str(
    "QSOLOGY_HTTP_USER_AGENT",
    default="qsology/2026.01 (+https://github.com/qsology/qsology)",
)

# Reference lists, refreshed weekly
REFERENCE_SOURCES = {
    "dxcc": env.str(
        "QSOLOGY_DXCC_URL", default="https://lotw.arrl.org/lotw/config.tq6"
    ),
    "pota": env.str(
        "QSOLOGY_POTA_URL", default="https://pota.app/all_parks_ext.csv"
    ),
    "sota": env.str(
        "QSOLOGY_SOTA_URL",
        default="https://storage.sota.org.uk/summitslist.csv",
    ),
    "iota": env.str(
        "QSOLOGY_IOTA_URL",
        default=(
            "https://www.iota-world.org/islands-on-the-air/downloads/"
            "download-file.html?path=fulllist.json"
        ),
    ),
}

# Address lookups use Nominatim (OpenStreetMap), free but limited to one
# request a second on the public server. Point this at a self hosted
# Nominatim to lift that
GEOCODER_URL = env.str(
    "QSOLOGY_GEOCODER_URL",
    default="https://nominatim.openstreetmap.org/search",
)
GEOCODER_MIN_INTERVAL = env.float("QSOLOGY_GEOCODER_MIN_INTERVAL", default=1.0)

# QRZ.com XML lookups, credentials are per user
QRZ_XML_URL = env.str(
    "QSOLOGY_QRZ_XML_URL", default="https://xmldata.qrz.com/xml/current/"
)
QRZ_AGENT = env.str("QSOLOGY_QRZ_AGENT", default="qsology-2026.01")

# Log exports are built by celery and kept for a while to download. They're
# never public, a download goes through the app, which checks who's asking
# and hands out a link that only works for a few minutes
EXPORT_RETENTION = timedelta(days=env.int("QSOLOGY_EXPORT_RETENTION_DAYS", 7))
EXPORT_DOWNLOAD_URL_TTL = env.int("QSOLOGY_EXPORT_DOWNLOAD_URL_TTL", 300)
# An export that hasn't started or finished by now is queued again
EXPORT_STALE_AFTER = timedelta(
    minutes=env.int("QSOLOGY_EXPORT_STALE_MINUTES", 60)
)
EXPORTS_S3_BUCKET = env.str("QSOLOGY_EXPORTS_S3_BUCKET", default="")
if EXPORTS_S3_BUCKET:
    # Any S3 compatible store: AWS, MinIO, Garage, R2...
    STORAGES["exports"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": EXPORTS_S3_BUCKET,
            "endpoint_url": env.str(
                "QSOLOGY_EXPORTS_S3_ENDPOINT_URL", default=None
            ),
            "region_name": env.str("QSOLOGY_EXPORTS_S3_REGION", default=None),
            "access_key": env.str(
                "QSOLOGY_EXPORTS_S3_ACCESS_KEY", default=None
            ),
            "secret_key": env.str(
                "QSOLOGY_EXPORTS_S3_SECRET_KEY", default=None
            ),
            "addressing_style": env.str(
                "QSOLOGY_EXPORTS_S3_ADDRESSING_STYLE", default=None
            ),
            "location": env.str("QSOLOGY_EXPORTS_S3_PREFIX", default="exports"),
            "signature_version": "s3v4",
            # Private, only reachable through signed links that expire
            "default_acl": "private",
            "querystring_auth": True,
            "querystring_expire": EXPORT_DOWNLOAD_URL_TTL,
            "custom_domain": None,
            "file_overwrite": False,
        },
    }
else:
    # Kept outside MEDIA_ROOT so it's never served as a static file
    STORAGES["exports"] = {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {
            "location": env.path(
                "QSOLOGY_EXPORTS_ROOT",
                default=str(BASE_DIR / "private/exports"),
            ),
        },
    }

# Notifications, sent through each of the channels a user prefers. Users
# can't choose yet, so everyone gets the defaults
NOTIFICATION_CHANNELS = {
    "log": "apps.notifications.channels.LogChannel",
}
NOTIFICATION_DEFAULT_CHANNELS = env.list(
    "QSOLOGY_NOTIFICATION_CHANNELS", default=["log"]
)

# Logging to stdout for the container runtime to collect, with the trace and
# span ids so logs can be matched up with traces
LOG_LEVEL = env.str("DJANGO_LOG_LEVEL", default="INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "trace": {"()": "config.logging.TraceContextFilter"},
    },
    "formatters": {
        "plain": {
            "format": (
                "%(asctime)s %(levelname)s %(name)s "
                "[trace=%(otelTraceID)s span=%(otelSpanID)s] %(message)s"
            ),
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "plain",
            "filters": ["trace"],
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
}
