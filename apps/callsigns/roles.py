"""Callsign sharing roles and capabilities"""

from django.db import models


class CallsignRole(models.TextChoices):
    """Roles a user can have on a callsign, lowest to highest

    The holder of a callsign is always the OWNER, other users are granted a
    role through a share. Specific permissions per role come later"""

    VIEWER = "viewer", "Viewer"
    LOGGER = "logger", "Logger"
    MANAGER = "manager", "Manager"
    OWNER = "owner", "Owner"


# Higher ranks include everything the lower ranks can do
RANKS = {
    CallsignRole.VIEWER: 10,
    CallsignRole.LOGGER: 20,
    CallsignRole.MANAGER: 30,
    CallsignRole.OWNER: 40,
}


def role_at_least(role: str | None, minimum: str) -> bool:
    """Is the role the same as, or higher than, the minimum?"""
    if role is None:
        return False
    return RANKS[CallsignRole(role)] >= RANKS[CallsignRole(minimum)]


def roles_at_least(minimum: str) -> list[CallsignRole]:
    """All roles the same as, or higher than, the minimum"""
    return [role for role in CallsignRole if role_at_least(role, minimum)]
