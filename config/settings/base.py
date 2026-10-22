from datetime import timedelta
from pathlib import Path

from environs import Env

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
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
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
    "default": env.dj_db_url("DJANGO_DATABASE_URL"),
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
]

THIRD_PARTY_APPS = []

LOCAL_APPS = [
    "apps.accounts",
    "apps.callsigns",
    "apps.locations",
    "apps.qrz",
    "apps.logbook",
    "apps.notifications",
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

# Celery
CELERY_BROKER_URL = env.str(
    "CELERY_BROKER_URL", default="redis://localhost:6379/0"
)
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
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
}

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
