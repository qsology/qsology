from apps.accounts.models import Theme, UIMode

# Anonymous visitors' theme is kept in their session
SESSION_THEME = "theme"


def theme_for(request) -> str:
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return user.theme
    session = getattr(request, "session", None)
    theme = session.get(SESSION_THEME) if session is not None else None
    return theme if theme in Theme.values else Theme.AUTO


def preferences(request) -> dict:
    user = getattr(request, "user", None)
    signed_in = user is not None and user.is_authenticated
    return {
        "theme": theme_for(request),
        "themes": Theme.choices,
        "advanced": signed_in and user.ui_mode == UIMode.ADVANCED,
    }
