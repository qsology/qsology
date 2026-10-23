from config.cache import REDIS_CACHE, from_url


class TestFromUrl:
    def test_redis_keeps_the_username(self):
        url = "redis://qsology:p%40ss@valkey-haproxy:6379/1"
        assert from_url(url) == {"BACKEND": REDIS_CACHE, "LOCATION": url}

    def test_redis_with_tls(self):
        url = "rediss://:secret@valkey:6380/1"
        assert from_url(url)["LOCATION"] == url

    def test_others_as_django_cache_url_reads_them(self):
        assert from_url("locmem://") == {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "",
        }
