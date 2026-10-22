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

LOCAL_APPS = ["apps.accounts", "apps.callsigns"]
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
}
