from apps.accounts.models import Theme, UIMode
from django import forms
from django.forms.renderers import TemplatesSetting


class ThemeForm(forms.Form):
    theme = forms.ChoiceField(choices=Theme.choices)


class UIModeForm(forms.Form):
    ui_mode = forms.ChoiceField(choices=UIMode.choices)


class Renderer(TemplatesSetting):
    """Fields laid out the way Pico expects, see templates/includes"""

    field_template_name = "includes/field.html"


def add_errors(form, exc) -> None:
    """Put a ValidationError from the services onto the form, against the
    field it's about when the form has that field"""
    if not hasattr(exc, "error_dict"):
        form.add_error(None, exc.messages)
        return
    for name, errors in exc.message_dict.items():
        form.add_error(name if name in form.fields else None, errors)
