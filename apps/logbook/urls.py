from apps.logbook import views
from django.urls import path

app_name = "logbook"

urlpatterns = [
    path("log/", views.log, name="log"),
    path("log/lookup/", views.lookup, name="lookup"),
    path("log/qsos/", views.qso_list, name="qso-list"),
    path("log/qsos/<uuid:uuid>/", views.qso_detail, name="qso"),
    path("log/qsos/<uuid:uuid>/delete/", views.qso_delete, name="qso-delete"),
    path(
        "log/qsos/<uuid:uuid>/restore/", views.qso_restore, name="qso-restore"
    ),
    path("log/qsos/<uuid:uuid>/checked/", views.qso_review, name="qso-review"),
    path("exports/", views.export_list, name="export-list"),
    path("exports/<uuid:pk>/", views.export_detail, name="export"),
    path(
        "exports/<uuid:pk>/download/",
        views.download_export,
        name="export-download",
    ),
    path("imports/", views.import_list, name="import-list"),
    path("imports/<int:pk>/", views.import_detail, name="import"),
    path(
        "imports/<int:pk>/records/<int:record_pk>/log/",
        views.import_record_log,
        name="import-record-log",
    ),
]
