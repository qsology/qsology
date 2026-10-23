"""The cache from a URL, as django-cache-url reads it, except for Redis

django-cache-url drops the username from redis://user:password@host, so
the client would log in as the default user with someone else's password.
Django's own Redis cache takes the URL as it is, so Redis URLs go to it
untouched."""

import django_cache_url

REDIS_CACHE = "django.core.cache.backends.redis.RedisCache"


def from_url(url: str) -> dict:
    if url.startswith(("redis://", "rediss://")):
        return {"BACKEND": REDIS_CACHE, "LOCATION": url}
    return django_cache_url.parse(url)
