from .base import *  # noqa: F403
from .base import env

# TLS ends at the gateway, which says so in X-Forwarded-Proto. The gateway
# is the only way in, so the header can be trusted
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = False
# The gateway redirects HTTP to HTTPS, and probes come in over plain HTTP
SECURE_SSL_REDIRECT = False
SILENCED_SYSTEM_CHECKS = ["security.W008"]
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool(
    "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True
)
SECURE_HSTS_PRELOAD = env.bool("DJANGO_SECURE_HSTS_PRELOAD", default=False)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"
