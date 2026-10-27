from datetime import UTC, datetime, time

from django import forms
from django.utils import timezone


def _start_of(day):
    return datetime.combine(day, time.min, tzinfo=UTC) if day else None


class AddCallsignForm(forms.Form):
    call = forms.CharField(
        label="Callsign",
        max_length=16,
        help_text="Without any prefix or suffix, e.g. M0ABC not M0ABC/P",
        widget=forms.TextInput(
            attrs={
                "autocapitalize": "characters",
                "autocomplete": "off",
                "spellcheck": "false",
            }
        ),
    )
    valid_from = forms.DateField(
        label="Held from",
        help_text="As YYYY-MM-DD",
        initial=lambda: timezone.now().date(),
    )
    valid_until = forms.DateField(
        label="Held until",
        required=False,
        help_text="Leave empty if you still hold it",
    )
    is_primary = forms.BooleanField(
        label="This is my main callsign", required=False
    )

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("valid_from"), cleaned.get("valid_until")
        if start and end and end <= start:
            self.add_error("valid_until", "This must be after the start")
        return cleaned

    @property
    def values(self) -> dict:
        return {
            "call": self.cleaned_data["call"],
            "valid_from": _start_of(self.cleaned_data["valid_from"]),
            "valid_until": _start_of(self.cleaned_data["valid_until"]),
            "is_primary": self.cleaned_data["is_primary"],
        }


class ChangeCallsignForm(forms.Form):
    valid_until = forms.DateField(
        label="Held until",
        required=False,
        help_text="As YYYY-MM-DD, empty if you still hold it",
    )
    is_primary = forms.BooleanField(
        label="This is my main callsign", required=False
    )

    @property
    def values(self) -> dict:
        return {
            "valid_until": _start_of(self.cleaned_data["valid_until"]),
            "is_primary": self.cleaned_data["is_primary"],
        }
