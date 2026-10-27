"""The account: profile, password, QRZ.com login, API keys and activity"""

from datetime import datetime, timedelta

from apps.accounts import forms
from apps.accounts.models import ApiKey
from apps.audit.models import AuditEntry
from apps.callsigns.services import primary_call
from apps.qrz.models import QRZCredential
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.forms import (
    PasswordChangeForm,
    PasswordResetForm,
    SetPasswordForm,
)
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.http import urlsafe_base64_decode
from ninja import Router, Schema, Status
from ninja.pagination import LimitOffsetPagination, paginate
from ninja.throttling import AnonRateThrottle

router = Router(tags=["account"])

# Signed out requests that send email or try tokens
SIGNED_OUT_LIMITS = [AnonRateThrottle("10/h")]


def form_error(form) -> ValidationError:
    return ValidationError(form.errors.as_data())


class Me(Schema):
    username: str
    display_name: str
    email: str
    theme: str
    ui_mode: str
    callsign: str


class MeChanges(Schema):
    display_name: str | None = None
    email: str | None = None
    theme: str | None = None
    ui_mode: str | None = None


def _me(user) -> Me:
    return Me(
        username=user.get_username(),
        display_name=user.display_name,
        email=user.email,
        theme=user.theme,
        ui_mode=user.ui_mode,
        callsign=primary_call(user),
    )


@router.get("/me", response=Me, url_name="me-get")
def me_get(request):
    """Who you are, and your preferences"""
    return _me(request.user)


@router.patch("/me", response=Me, url_name="me-update")
def me_update(request, changes: MeChanges):
    """Change your profile or preferences, what isn't sent stays as it is"""
    user = request.user
    data = {name: getattr(user, name) for name in forms.ProfileForm.Meta.fields}
    data.update(changes.dict(exclude_unset=True))
    form = forms.ProfileForm(data, instance=user)
    if not form.is_valid():
        raise form_error(form)
    form.save()
    return _me(user)


class PasswordChange(Schema):
    old_password: str
    new_password: str


@router.post("/me/password", response={204: None}, url_name="me-password")
def me_password(request, payload: PasswordChange):
    """Change your password. API keys keep working"""
    form = PasswordChangeForm(
        request.user,
        {
            "old_password": payload.old_password,
            "new_password1": payload.new_password,
            "new_password2": payload.new_password,
        },
    )
    if not form.is_valid():
        raise form_error(form)
    form.save()
    if request.session.session_key:
        update_session_auth_hash(request, form.user)
    return Status(204, None)


class PasswordReset(Schema):
    email: str


@router.post(
    "/auth/password-reset",
    response={202: None},
    auth=None,
    throttle=SIGNED_OUT_LIMITS,
    url_name="password-reset",
)
def password_reset(request, payload: PasswordReset):
    """Email a link to set a new password. The answer is the same whether
    there's an account with the address or not"""
    form = PasswordResetForm({"email": payload.email})
    if not form.is_valid():
        raise form_error(form)
    form.save(
        request=request,
        use_https=request.is_secure(),
        email_template_name="registration/password_reset_email.txt",
    )
    return Status(202, None)


class PasswordResetConfirm(Schema):
    uid: str
    token: str
    new_password: str


@router.post(
    "/auth/password-reset/confirm",
    response={204: None},
    auth=None,
    throttle=SIGNED_OUT_LIMITS,
    url_name="password-reset-confirm",
)
def password_reset_confirm(request, payload: PasswordResetConfirm):
    """Set a new password with the uid and token from the emailed link"""
    invalid = ValidationError({"token": ["This link doesn't work any more"]})
    try:
        pk = urlsafe_base64_decode(payload.uid).decode()
        user = get_user_model()._default_manager.get(pk=pk)
    except Exception as exc:
        raise invalid from exc
    if not default_token_generator.check_token(user, payload.token):
        raise invalid
    form = SetPasswordForm(
        user,
        {
            "new_password1": payload.new_password,
            "new_password2": payload.new_password,
        },
    )
    if not form.is_valid():
        raise form_error(form)
    form.save()
    return Status(204, None)


class QRZLogin(Schema):
    username: str
    is_enabled: bool
    last_error: str


class QRZLoginChanges(Schema):
    username: str
    # Empty keeps the password already saved
    password: str = ""
    is_enabled: bool = True


@router.get("/me/qrz", response={200: QRZLogin, 404: None}, url_name="qrz-get")
def qrz_get(request):
    """Your QRZ.com login, without its password"""
    credential = QRZCredential.objects.filter(user=request.user).first()
    if credential is None:
        return Status(404, None)
    return credential


@router.put("/me/qrz", response=QRZLogin, url_name="qrz-set")
def qrz_set(request, payload: QRZLoginChanges):
    """Set your QRZ.com login, used to look up callsigns"""
    credential = QRZCredential.objects.filter(user=request.user).first()
    form = forms.QRZForm(
        payload.dict(),
        instance=credential or QRZCredential(user=request.user),
    )
    if not form.is_valid():
        raise form_error(form)
    return form.save()


@router.delete("/me/qrz", response={204: None}, url_name="qrz-delete")
def qrz_delete(request):
    QRZCredential.objects.filter(user=request.user).delete()
    return Status(204, None)


class Key(Schema):
    id: int
    name: str
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None


class NewKey(Key):
    # The whole key, only ever given here
    key: str


class KeyRequest(Schema):
    name: str
    # Days until it stops working, none for never
    expires_in_days: int | None = None


@router.get("/me/api-keys", response=list[Key], url_name="api-key-list")
def api_key_list(request):
    return request.user.api_keys.all()


@router.post("/me/api-keys", response={201: NewKey}, url_name="api-key-create")
def api_key_create(request, payload: KeyRequest):
    """Make a key. The response is the only time the key is given"""
    name = payload.name.strip()
    errors = {}
    if not name or len(name) > 100:
        errors["name"] = ["Name the key, in at most 100 characters"]
    days = payload.expires_in_days
    if days is not None and not 1 <= days <= 3650:
        errors["expires_in_days"] = ["Between 1 and 3650 days, or none"]
    if errors:
        raise ValidationError(errors)
    expires_at = timezone.now() + timedelta(days=days) if days else None
    key, full = ApiKey.issue(request.user, name, expires_at=expires_at)
    return Status(201, NewKey(key=full, **Key.model_validate(key).dict()))


@router.delete(
    "/me/api-keys/{key_id}", response={204: None}, url_name="api-key-revoke"
)
def api_key_revoke(request, key_id: int):
    """Revoke a key, it stops working straight away"""
    get_object_or_404(ApiKey, pk=key_id, user=request.user).revoke()
    return Status(204, None)


class Activity(Schema):
    action: str
    summary: str
    object_type: str
    object_id: str
    data: dict
    created_at: datetime


@router.get("/activity", response=list[Activity], url_name="activity-list")
@paginate(LimitOffsetPagination)
def activity_list(request):
    """What background work has done for you, such as building exports,
    newest first"""
    return AuditEntry.objects.for_user(request.user)
