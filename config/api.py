"""The API, /api/v1/

Everything the pages do can be done here too: tests/web/test_parity.py
checks every page names its API operations (apps.web.parity). Programs use
an API key, sent as "Authorization: Bearer qsk_...", made on the API keys
page. The site's own scripts use the signed in session, with its CSRF token.

The OpenAPI description is at /api/v1/openapi.json. There's no interactive
docs page: ninja's loads its scripts from a CDN, which the site's CSP
doesn't allow"""

from apps.accounts.api import router as accounts_router
from apps.accounts.models import ApiKey
from apps.callsigns.api import router as callsigns_router
from apps.logbook.api import router as logbook_router
from apps.logbook.models import ProtectedOperation, QSOConflict
from apps.logbook.services import SanityCheckError
from django.core.exceptions import ValidationError
from ninja import NinjaAPI
from ninja.security import HttpBearer, SessionAuth


class ApiKeyAuth(HttpBearer):
    def authenticate(self, request, token):
        user = ApiKey.authenticate(token)
        if user is not None:
            # So everything after sees who it is, as with a session
            request.user = user
        return user


api = NinjaAPI(
    title="qsology",
    version="1",
    description=__doc__,
    urls_namespace="api-1",
    auth=[ApiKeyAuth(), SessionAuth()],
    docs_url=None,
)


@api.exception_handler(ValidationError)
def validation_error(request, exc):
    if hasattr(exc, "error_dict"):
        errors = exc.message_dict
    else:
        errors = {"__all__": exc.messages}
    return api.create_response(request, {"errors": errors}, status=422)


@api.exception_handler(SanityCheckError)
def sanity_warnings(request, exc):
    """The QSO has warnings, send it again with accept_warnings to log it
    marked for review"""
    return api.create_response(
        request,
        {"warnings": [issue.as_dict() for issue in exc.issues]},
        status=422,
    )


@api.exception_handler(QSOConflict)
def conflict(request, exc):
    return api.create_response(
        request,
        {"detail": "The QSO has changed since that version, fetch it again"},
        status=409,
    )


@api.exception_handler(ProtectedOperation)
def protected(request, exc):
    return api.create_response(request, {"detail": str(exc)}, status=409)


api.add_router("", accounts_router)
api.add_router("/callsigns", callsigns_router)
api.add_router("", logbook_router)
