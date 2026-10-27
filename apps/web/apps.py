from django.apps import AppConfig


class WebConfig(AppConfig):
    """The site's pages: the base templates, landing page and preferences
    every other app's pages build on. See docs/web.md"""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.web"
    label = "web"
