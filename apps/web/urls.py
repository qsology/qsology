from apps.web import views
from django.urls import path

app_name = "web"

urlpatterns = [
    path("", views.home, name="home"),
    path("preferences/theme/", views.set_theme, name="set-theme"),
    path("preferences/view/", views.set_ui_mode, name="set-ui-mode"),
]
