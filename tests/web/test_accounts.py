"""Signing in and out, passwords, the profile, QRZ.com login and API keys,
without scripts (the text browser) and through the API"""

import re

import pytest
from apps.accounts.models import ApiKey
from apps.qrz.models import QRZCredential
from django.urls import reverse

from tests.web.conftest import PASSWORD

pytestmark = pytest.mark.django_db


def bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


class TestSigningIn:
    def test_text(self, browser, user):
        browser.get("/")
        browser.click("Sign in")
        browser.fill("Username", user.username)
        browser.fill("Password", PASSWORD)
        browser.press("Sign in")
        assert browser.path == "/"
        assert browser.h1.startswith("Hello")

    def test_back_where_they_were_going(self, browser, user):
        browser.get(reverse("logbook:qso-list"))
        assert browser.path == reverse("accounts:login")
        browser.fill("Username", user.username)
        browser.fill("Password", PASSWORD)
        browser.press("Sign in")
        assert browser.path == reverse("logbook:qso-list")

    def test_wrong_password(self, browser, user):
        browser.get(reverse("accounts:login"))
        browser.fill("Username", user.username)
        browser.fill("Password", "not it")
        browser.press("Sign in")
        assert browser.status == 200
        assert browser.errors()
        # What was typed, apart from the password, is still there
        assert browser.value("Username") == user.username
        assert browser.value("Password") == ""

    def test_api_uses_keys_not_signing_in(self, client, user):
        assert client.get("/api/v1/me").status_code == 401
        _, key = ApiKey.issue(user, "test")
        response = client.get("/api/v1/me", headers=bearer(key))
        assert response.json()["username"] == user.username


class TestSigningOut:
    def test_text(self, signed_in):
        signed_in.get("/")
        signed_in.press("Sign out")
        assert signed_in.path == "/"
        assert "Sign in" in signed_in

    def test_only_by_post(self, signed_in, client):
        assert client.get(reverse("accounts:logout")).status_code == 405


class TestChangingPassword:
    def test_text(self, signed_in, user):
        signed_in.get("/")
        signed_in.click("Profile")
        signed_in.click("Password")
        signed_in.fill("Old password", PASSWORD)
        signed_in.fill("New password", "a-new-long-passphrase")
        signed_in.fill("New password confirmation", "a-new-long-passphrase")
        signed_in.press("Change password")
        assert signed_in.h1 == "Password changed"
        user.refresh_from_db()
        assert user.check_password("a-new-long-passphrase")

    def test_wrong_old_password(self, signed_in):
        signed_in.get(reverse("accounts:password-change"))
        signed_in.fill("Old password", "wrong")
        signed_in.fill("New password", "a-new-long-passphrase")
        signed_in.fill("New password confirmation", "a-new-long-passphrase")
        signed_in.press("Change password")
        assert signed_in.field_errors("Old password")

    def test_api(self, client, user):
        _, key = ApiKey.issue(user, "test")
        response = client.post(
            "/api/v1/me/password",
            {"old_password": PASSWORD, "new_password": "a-new-long-passphrase"},
            content_type="application/json",
            headers=bearer(key),
        )
        assert response.status_code == 204
        user.refresh_from_db()
        assert user.check_password("a-new-long-passphrase")
        # The key still works
        assert client.get("/api/v1/me", headers=bearer(key)).status_code == 200

    def test_api_wrong_old_password(self, client, user):
        _, key = ApiKey.issue(user, "test")
        response = client.post(
            "/api/v1/me/password",
            {"old_password": "wrong", "new_password": "a-new-long-passphrase"},
            content_type="application/json",
            headers=bearer(key),
        )
        assert response.status_code == 422
        assert "old_password" in response.json()["errors"]


def reset_link(mailoutbox) -> str:
    body = mailoutbox[-1].body
    return re.search(r"https?://[^/]+(/\S+)", body).group(1)


class TestResettingPassword:
    def test_text(self, browser, user, mailoutbox):
        browser.get(reverse("accounts:login"))
        browser.click("Forgotten your password?")
        browser.fill("Email", user.email)
        browser.press("Send the link")
        assert browser.h1 == "Check your email"
        browser.get(reset_link(mailoutbox))
        browser.fill("New password", "a-new-long-passphrase")
        browser.fill("New password confirmation", "a-new-long-passphrase")
        browser.press("Set password")
        assert browser.h1 == "Password set"
        user.refresh_from_db()
        assert user.check_password("a-new-long-passphrase")

    def test_unknown_address_looks_the_same(self, browser, mailoutbox):
        browser.get(reverse("accounts:password-reset"))
        browser.fill("Email", "nobody@qsology.test")
        browser.press("Send the link")
        assert browser.h1 == "Check your email"
        assert not mailoutbox

    def test_api(self, client, user, mailoutbox):
        response = client.post(
            "/api/v1/auth/password-reset",
            {"email": user.email},
            content_type="application/json",
        )
        assert response.status_code == 202
        link = reset_link(mailoutbox)
        uid, token = link.rstrip("/").split("/")[-2:]
        response = client.post(
            "/api/v1/auth/password-reset/confirm",
            {"uid": uid, "token": token, "new_password": "a-new-long-pass"},
            content_type="application/json",
        )
        assert response.status_code == 204
        user.refresh_from_db()
        assert user.check_password("a-new-long-pass")

    def test_api_bad_token(self, client, user):
        response = client.post(
            "/api/v1/auth/password-reset/confirm",
            {"uid": "MQ", "token": "nope", "new_password": "a-new-long-pass"},
            content_type="application/json",
        )
        assert response.status_code == 422


class TestProfile:
    def test_text(self, signed_in, user):
        signed_in.get("/")
        signed_in.click("Profile")
        signed_in.fill("Display name", "Welby")
        signed_in.fill("Email", "new@qsology.test")
        signed_in.press("Save")
        assert "Your profile has been saved." in signed_in.messages()
        user.refresh_from_db()
        assert (user.display_name, user.email) == ("Welby", "new@qsology.test")

    def test_bad_email_keeps_what_was_typed(self, signed_in):
        signed_in.get(reverse("accounts:profile"))
        signed_in.fill("Display name", "Welby")
        signed_in.fill("Email", "not an address")
        signed_in.press("Save")
        assert signed_in.status == 422
        assert signed_in.field_errors("Email")
        assert signed_in.value("Display name") == "Welby"

    def test_api(self, client, user):
        _, key = ApiKey.issue(user, "test")
        response = client.patch(
            "/api/v1/me",
            {"display_name": "Welby", "theme": "dark"},
            content_type="application/json",
            headers=bearer(key),
        )
        assert response.status_code == 200
        assert response.json()["display_name"] == "Welby"
        user.refresh_from_db()
        assert (user.display_name, user.theme) == ("Welby", "dark")
        # What wasn't sent is left alone
        assert user.email == "m0wme@qsology.test"

    def test_api_bad_value(self, client, user):
        _, key = ApiKey.issue(user, "test")
        response = client.patch(
            "/api/v1/me",
            {"theme": "purple"},
            content_type="application/json",
            headers=bearer(key),
        )
        assert response.status_code == 422
        assert "theme" in response.json()["errors"]


class TestQRZLogin:
    def test_text(self, signed_in, user):
        signed_in.get(reverse("accounts:profile"))
        signed_in.click("QRZ.com lookups")
        signed_in.fill("QRZ.com username", "m0wme")
        signed_in.fill("Password", "secret")
        signed_in.press("Save")
        credential = QRZCredential.objects.get(user=user)
        assert (credential.username, credential.password) == ("m0wme", "secret")
        # The password is never shown again, and empty keeps it
        assert signed_in.value("Password") == ""
        signed_in.fill("QRZ.com username", "m0wme2")
        signed_in.press("Save")
        credential.refresh_from_db()
        assert (credential.username, credential.password) == (
            "m0wme2",
            "secret",
        )

    def test_password_needed_the_first_time(self, signed_in):
        signed_in.get(reverse("accounts:qrz"))
        signed_in.fill("QRZ.com username", "m0wme")
        signed_in.press("Save")
        assert signed_in.field_errors("Password")

    def test_removing(self, signed_in, user):
        QRZCredential.objects.create(user=user, username="m0wme", password="x")
        signed_in.get(reverse("accounts:qrz"))
        signed_in.press("Remove my QRZ.com login")
        assert not QRZCredential.objects.filter(user=user).exists()

    def test_api(self, client, user):
        _, key = ApiKey.issue(user, "test")
        assert (
            client.get("/api/v1/me/qrz", headers=bearer(key)).status_code == 404
        )
        response = client.put(
            "/api/v1/me/qrz",
            {"username": "m0wme", "password": "secret"},
            content_type="application/json",
            headers=bearer(key),
        )
        assert response.status_code == 200
        assert "password" not in response.json()
        assert client.get("/api/v1/me/qrz", headers=bearer(key)).json()[
            "username"
        ] == ("m0wme")
        response = client.delete("/api/v1/me/qrz", headers=bearer(key))
        assert response.status_code == 204
        assert not QRZCredential.objects.exists()


class TestApiKeys:
    def test_text(self, signed_in, client, user):
        signed_in.get(reverse("accounts:profile"))
        signed_in.click("API keys")
        signed_in.fill("Name", "Laptop logger")
        signed_in.choose("Stops working", "In 30 days")
        signed_in.press("Make key")
        key = signed_in.value("Key")
        assert key.startswith("qsk_")
        assert signed_in.table("Your API keys")[0]["Name"] == "Laptop logger"
        client.logout()
        assert client.get("/api/v1/me", headers=bearer(key)).status_code == 200

    def test_shown_once(self, signed_in, user):
        signed_in.get(reverse("accounts:api-keys"))
        signed_in.fill("Name", "Laptop logger")
        signed_in.press("Make key")
        key = signed_in.value("Key")
        signed_in.get(reverse("accounts:api-keys"))
        assert key not in signed_in.response.content.decode()
        assert ApiKey.objects.get().secret_hash not in key

    def test_never_cached(self, signed_in):
        signed_in.get(reverse("accounts:api-keys"))
        assert "no-store" in signed_in.response["Cache-Control"]

    def test_revoking(self, signed_in, client, user):
        _, key = ApiKey.issue(user, "Laptop logger")
        signed_in.get(reverse("accounts:api-keys"))
        signed_in.press("Revoke Laptop logger")
        assert "no longer works" in " ".join(signed_in.messages())
        client.logout()
        assert client.get("/api/v1/me", headers=bearer(key)).status_code == 401

    def test_several_each_on_its_own(self, client, user):
        first, first_key = ApiKey.issue(user, "one")
        _, second_key = ApiKey.issue(user, "two")
        first.revoke()
        assert (
            client.get("/api/v1/me", headers=bearer(first_key)).status_code
            == 401
        )
        assert (
            client.get("/api/v1/me", headers=bearer(second_key)).status_code
            == 200
        )

    def test_someone_elses_cant_be_revoked(self, signed_in, client, other_user):
        theirs, _ = ApiKey.issue(other_user, "theirs")
        response = client.post(
            reverse("accounts:api-key-revoke", args=[theirs.pk])
        )
        assert response.status_code == 404

    def test_api(self, client, user):
        _, key = ApiKey.issue(user, "first")
        response = client.post(
            "/api/v1/me/api-keys",
            {"name": "script", "expires_in_days": 30},
            content_type="application/json",
            headers=bearer(key),
        )
        assert response.status_code == 201
        made = response.json()
        assert made["key"].startswith("qsk_")
        listed = client.get("/api/v1/me/api-keys", headers=bearer(key)).json()
        assert {each["name"] for each in listed} == {"first", "script"}
        assert all("key" not in each for each in listed)
        response = client.delete(
            f"/api/v1/me/api-keys/{made['id']}", headers=bearer(key)
        )
        assert response.status_code == 204
        assert (
            client.get("/api/v1/me", headers=bearer(made["key"])).status_code
            == 401
        )

    def test_wrong_or_mangled_keys(self, client, user):
        _, key = ApiKey.issue(user, "test")
        for wrong in (key[:-1], key + "x", "qsk_nope_nope", "nonsense", ""):
            assert (
                client.get("/api/v1/me", headers=bearer(wrong)).status_code
                == 401
            )

    def test_inactive_users_keys_stop(self, client, user):
        _, key = ApiKey.issue(user, "test")
        user.schedule_deletion()
        assert client.get("/api/v1/me", headers=bearer(key)).status_code == 401
