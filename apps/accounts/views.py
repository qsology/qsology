from apps.accounts import forms
from apps.accounts.models import ApiKey
from apps.qrz.models import QRZCredential
from apps.web.parity import api
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

API_KEYS_WITHOUT_SIGNING_IN = (
    "The API doesn't sign in, it's used with an API key made on the API "
    "keys page or with api-key-create"
)
JUST_SAYS_DONE = "Only says the step before worked"

login = api(reason=API_KEYS_WITHOUT_SIGNING_IN)(
    auth_views.LoginView.as_view(redirect_authenticated_user=True)
)
logout = api("api-key-revoke", reason=API_KEYS_WITHOUT_SIGNING_IN)(
    auth_views.LogoutView.as_view()
)
password_change = api("me-password")(
    auth_views.PasswordChangeView.as_view(
        success_url=reverse_lazy("accounts:password-change-done")
    )
)
password_change_done = api(reason=JUST_SAYS_DONE)(
    auth_views.PasswordChangeDoneView.as_view()
)
password_reset = api("password-reset")(
    auth_views.PasswordResetView.as_view(
        success_url=reverse_lazy("accounts:password-reset-done"),
        email_template_name="registration/password_reset_email.txt",
    )
)
password_reset_done = api(reason=JUST_SAYS_DONE)(
    auth_views.PasswordResetDoneView.as_view()
)
password_reset_confirm = api("password-reset-confirm")(
    auth_views.PasswordResetConfirmView.as_view(
        success_url=reverse_lazy("accounts:password-reset-complete")
    )
)
password_reset_complete = api(reason=JUST_SAYS_DONE)(
    auth_views.PasswordResetCompleteView.as_view()
)


@require_http_methods(["GET", "POST"])
@login_required
@api("me-get", "me-update")
def profile(request):
    form = forms.ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Your profile has been saved.")
        return redirect("accounts:profile")
    return render(
        request,
        "accounts/profile.html",
        {"form": form},
        status=422 if form.errors else 200,
    )


@require_http_methods(["GET", "POST"])
@login_required
@api("qrz-get", "qrz-set")
def qrz(request):
    credential = QRZCredential.objects.filter(user=request.user).first()
    form = forms.QRZForm(
        request.POST or None,
        instance=credential or QRZCredential(user=request.user),
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Your QRZ.com login has been saved.")
        return redirect("accounts:qrz")
    return render(
        request,
        "accounts/qrz.html",
        {"form": form, "credential": credential},
        status=422 if form.errors else 200,
    )


@require_POST
@login_required
@api("qrz-delete")
def qrz_delete(request):
    QRZCredential.objects.filter(user=request.user).delete()
    messages.success(request, "Your QRZ.com login has been removed.")
    return redirect("accounts:qrz")


# The new key is on the page, which mustn't be kept anywhere
@never_cache
@require_http_methods(["GET", "POST"])
@login_required
@api("api-key-list", "api-key-create")
def api_keys(request):
    form = forms.ApiKeyForm(request.POST or None)
    new_key = None
    if request.method == "POST" and form.is_valid():
        _, new_key = ApiKey.issue(
            request.user,
            form.cleaned_data["name"],
            expires_at=form.expires_at,
        )
        form = forms.ApiKeyForm()
    return render(
        request,
        "accounts/api_keys.html",
        {
            "form": form,
            "new_key": new_key,
            "keys": request.user.api_keys.all(),
        },
        status=422 if form.errors else 200,
    )


@require_POST
@login_required
@api("api-key-revoke")
def api_key_revoke(request, pk):
    key = get_object_or_404(ApiKey, pk=pk, user=request.user)
    key.revoke()
    messages.success(
        request, f"The key “{key.name}” has been revoked, it no longer works."
    )
    return redirect("accounts:api-keys")
