"""Requests from htmx, or the page's own scripts, get part of a page back

Every page works without them, these only decide whether a view sends the
whole page or the part that changed"""

from functools import wraps

from django.contrib.auth.decorators import login_required as _login_required
from django.http import HttpResponse


def is_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


def login_required(view):
    """login_required, but a script's request gets 401 rather than being
    sent to the sign in page, so it knows to say so rather than swap the
    sign in page into the one it's on"""
    redirecting = _login_required(view)

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if is_htmx(request) and not request.user.is_authenticated:
            return HttpResponse("Signed out", status=401)
        return redirecting(request, *args, **kwargs)

    return wrapper
