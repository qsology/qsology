"""Root URL Configuration"""

from django.urls import include, path

urlpatterns = [
    path("logbook/", include("apps.logbook.urls")),
]
