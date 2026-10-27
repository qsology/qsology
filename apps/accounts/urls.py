from apps.accounts import views
from django.urls import path

app_name = "accounts"

urlpatterns = [
    path("sign-in/", views.login, name="login"),
    path("sign-out/", views.logout, name="logout"),
    path("password/", views.password_change, name="password-change"),
    path(
        "password/changed/",
        views.password_change_done,
        name="password-change-done",
    ),
    path("password/reset/", views.password_reset, name="password-reset"),
    path(
        "password/reset/sent/",
        views.password_reset_done,
        name="password-reset-done",
    ),
    path(
        "password/reset/<uidb64>/<token>/",
        views.password_reset_confirm,
        name="password-reset-confirm",
    ),
    path(
        "password/reset/done/",
        views.password_reset_complete,
        name="password-reset-complete",
    ),
    path("", views.profile, name="profile"),
    path("qrz/", views.qrz, name="qrz"),
    path("qrz/remove/", views.qrz_delete, name="qrz-delete"),
    path("api-keys/", views.api_keys, name="api-keys"),
    path(
        "api-keys/<int:pk>/revoke/",
        views.api_key_revoke,
        name="api-key-revoke",
    ),
]
