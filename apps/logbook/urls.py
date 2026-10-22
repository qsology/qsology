from apps.logbook import views
from django.urls import path

app_name = "logbook"

urlpatterns = [
    path(
        "exports/<uuid:pk>/download/",
        views.download_export,
        name="export-download",
    ),
]
