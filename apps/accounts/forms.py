from datetime import timedelta

from apps.accounts.models import User
from apps.qrz.models import QRZCredential
from django import forms
from django.utils import timezone


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("display_name", "email", "theme", "ui_mode")
        labels = {"theme": "Colours"}
        widgets = {
            "display_name": forms.TextInput(attrs={"autocomplete": "name"}),
            "email": forms.EmailInput(attrs={"autocomplete": "email"}),
        }


class QRZForm(forms.ModelForm):
    """The QRZ.com login used to look up callsigns. The password is never
    shown again, leave it empty to keep the one saved"""

    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "off"}),
        required=False,
        help_text="Leave empty to keep the password already saved",
    )

    class Meta:
        model = QRZCredential
        fields = ("username", "password", "is_enabled")
        labels = {
            "username": "QRZ.com username",
            "is_enabled": "Look up callsigns on QRZ.com",
        }
        widgets = {
            "username": forms.TextInput(
                attrs={"autocomplete": "off", "spellcheck": "false"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields["password"].required = True
            self.fields["password"].help_text = ""

    def clean_password(self):
        password = self.cleaned_data["password"]
        if not password and self.instance.pk:
            return self.instance.password
        return password


class ApiKeyForm(forms.Form):
    EXPIRY_CHOICES = (
        ("", "Never"),
        ("30", "In 30 days"),
        ("90", "In 90 days"),
        ("365", "In a year"),
    )

    name = forms.CharField(
        max_length=100,
        help_text="What will use it, e.g. the logging program on my laptop",
    )
    expires = forms.ChoiceField(
        choices=EXPIRY_CHOICES, required=False, label="Stops working"
    )

    @property
    def expires_at(self):
        days = self.cleaned_data.get("expires")
        return timezone.now() + timedelta(days=int(days)) if days else None
