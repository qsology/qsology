from apps.audit.models import AuditEntry
from apps.logbook.models import QSO
from apps.web import forms
from apps.web.context_processors import SESSION_THEME
from apps.web.parity import api
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

# Fields never put back into a page, a password has to be typed again
SECRET_FIELDS = ("password", "secret", "token")
CSRF_FIELD = "csrfmiddlewaretoken"


def _back(request, default: str = "web:home") -> HttpResponseRedirect:
    """Back to the page the form was on, if it's one of ours"""
    target = request.POST.get("next") or request.GET.get("next")
    if not target or not url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        target = reverse(default)
    return HttpResponseRedirect(target)


@require_GET
@api("me-get", "activity-list")
def home(request):
    """The landing page, or for someone signed in, where they are"""
    if not request.user.is_authenticated:
        return render(request, "web/landing.html")
    qsos = QSO.objects.filter(owner=request.user)
    return render(
        request,
        "web/home.html",
        {
            "qso_count": qsos.count(),
            "needs_review": qsos.needs_review().count(),
            "recent": qsos.order_by("-time_on", "-id")[:5],
            "activity": AuditEntry.objects.for_user(request.user)[:5],
        },
    )


@require_POST
@api("me-update", reason="Signed out, the theme is only kept in the browser")
def set_theme(request):
    form = forms.ThemeForm(request.POST)
    if form.is_valid():
        theme = form.cleaned_data["theme"]
        if request.user.is_authenticated:
            request.user.theme = theme
            request.user.save(update_fields=["theme"])
        else:
            request.session[SESSION_THEME] = theme
    return _back(request)


@require_POST
@login_required
@api("me-update")
def set_ui_mode(request):
    form = forms.UIModeForm(request.POST)
    if form.is_valid():
        request.user.ui_mode = form.cleaned_data["ui_mode"]
        request.user.save(update_fields=["ui_mode"])
    return _back(request)


def _same_origin(request) -> bool:
    """Was the form sent from one of our own pages?"""
    source = request.headers.get("Origin") or request.headers.get("Referer")
    return bool(source) and url_has_allowed_host_and_scheme(
        source,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    )


def csrf_failure(request, reason=""):
    """A form that couldn't be checked, usually because it was loaded
    before signing in again. What was typed is shown with a button to send
    it again, so nothing is lost. Only for our own pages, and never with a
    password or file, which have to be given again"""
    names = [name for name in request.POST if name != CSRF_FIELD]
    has_secrets = any(
        secret in name for name in names for secret in SECRET_FIELDS
    )
    fields = [
        (name, value) for name in names for value in request.POST.getlist(name)
    ]
    resend = (
        request.method == "POST"
        and _same_origin(request)
        and fields
        and not has_secrets
        and not request.FILES
    )
    return render(
        request,
        "web/csrf_failure.html",
        {
            "fields": fields if resend else [],
            "action": request.get_full_path(),
            "had_file": bool(request.FILES),
        },
        status=403,
    )
