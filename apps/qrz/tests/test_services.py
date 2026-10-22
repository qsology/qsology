import pytest
from apps.qrz import services
from apps.qrz.client import QRZAuthError, QRZNotFound
from apps.qrz.models import QRZCredential

from .conftest import SESSION_KEY, expired, found, login_failed, login_ok
from .test_client import FakeFetch

pytestmark = pytest.mark.django_db


class TestCredential:
    def test_str(self, credential):
        assert str(credential) == "QRZ login m0wme for m0wme"

    def test_one_per_user(self, credential, user):
        with pytest.raises(Exception):  # noqa: B017
            QRZCredential.objects.create(user=user, username="x", password="y")

    @pytest.mark.parametrize(
        ("field", "value"), [("username", "g4abc"), ("password", "new")]
    )
    def test_new_login_clears_session(self, credential, field, value):
        credential.session_key = SESSION_KEY
        credential.save()
        setattr(credential, field, value)
        credential.save()
        credential.refresh_from_db()
        assert credential.session_key == ""

    def test_other_changes_keep_session(self, credential):
        credential.session_key = SESSION_KEY
        credential.save()
        credential.is_enabled = False
        credential.save()
        credential.refresh_from_db()
        assert credential.session_key == SESSION_KEY

    def test_username_required(self, user):
        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            QRZCredential.objects.create(user=user, username="", password="x")


class TestLookup:
    def test_no_credential(self, user):
        with pytest.raises(services.QRZNotConfigured):
            services.lookup(user, "AA7BQ", fetch=FakeFetch())

    def test_disabled_credential(self, user, credential):
        credential.is_enabled = False
        credential.save()
        with pytest.raises(services.QRZNotConfigured):
            services.lookup(user, "AA7BQ", fetch=FakeFetch())

    def test_only_uses_own_credential(self, other_user, credential):
        with pytest.raises(services.QRZNotConfigured):
            services.lookup(other_user, "AA7BQ", fetch=FakeFetch())

    def test_saves_new_session_key(self, user, credential):
        fetch = FakeFetch(login_ok(), found())
        result = services.lookup(user, "AA7BQ", fetch=fetch)

        assert result.callsign == "AA7BQ"
        assert fetch.calls[0]["username"] == "m0wme"
        assert fetch.calls[0]["password"] == "hunter2"
        credential.refresh_from_db()
        assert credential.session_key == SESSION_KEY

    def test_reuses_saved_session_key(self, user, credential):
        credential.session_key = "saved"
        credential.save()
        fetch = FakeFetch(found())
        services.lookup(user, "AA7BQ", fetch=fetch)
        assert fetch.calls[0]["s"] == "saved"

    def test_renewed_session_saved(self, user, credential):
        credential.session_key = "stale"
        credential.save()
        fetch = FakeFetch(expired(), login_ok("fresh"), found())
        services.lookup(user, "AA7BQ", fetch=fetch)
        credential.refresh_from_db()
        assert credential.session_key == "fresh"

    def test_auth_failure_recorded(self, user, credential):
        credential.session_key = "stale"
        credential.save()
        fetch = FakeFetch(expired(), login_failed())
        with pytest.raises(QRZAuthError):
            services.lookup(user, "AA7BQ", fetch=fetch)
        credential.refresh_from_db()
        assert credential.session_key == ""
        assert credential.last_error == "Username/password incorrect"

    def test_success_clears_last_error(self, user, credential):
        credential.last_error = "Username/password incorrect"
        credential.save()
        services.lookup(user, "AA7BQ", fetch=FakeFetch(login_ok(), found()))
        credential.refresh_from_db()
        assert credential.last_error == ""

    def test_not_found_keeps_session(self, user, credential):
        from .conftest import not_found

        fetch = FakeFetch(login_ok(), not_found())
        with pytest.raises(QRZNotFound):
            services.lookup(user, "XX9XXX", fetch=fetch)
        credential.refresh_from_db()
        # The login worked, so the session is kept for the next lookup
        assert credential.session_key == SESSION_KEY
        assert credential.last_error == ""

    def test_network_error_keeps_last_error(self, user, credential):
        from apps.common import http
        from apps.qrz.client import QRZError

        credential.session_key = "saved"
        credential.last_error = "earlier"
        credential.save()
        fetch = FakeFetch(http.HTTPError("timed out"))
        with pytest.raises(QRZError):
            services.lookup(user, "AA7BQ", fetch=fetch)
        credential.refresh_from_db()
        assert credential.session_key == "saved"
        assert credential.last_error == "earlier"
