"""Root URL Configuration"""

from django.urls import include, path

from config.api import api

urlpatterns = [
    path("", include("apps.web.urls")),
    path("account/", include("apps.accounts.urls")),
    path("callsigns/", include("apps.callsigns.urls")),
    path("", include("apps.logbook.urls")),
    path("api/v1/", api.urls),
]
