from apps.callsigns import views
from django.urls import path

app_name = "callsigns"

urlpatterns = [
    path("", views.callsign_list, name="list"),
    path("<int:pk>/", views.callsign_change, name="change"),
    path("<int:pk>/end/", views.callsign_end, name="end"),
]
