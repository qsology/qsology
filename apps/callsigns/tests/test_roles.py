import pytest
from apps.callsigns.roles import CallsignRole, role_at_least, roles_at_least


class TestCallsignRole:
    def test_roles(self):
        assert list(CallsignRole) == [
            CallsignRole.VIEWER,
            CallsignRole.LOGGER,
            CallsignRole.MANAGER,
            CallsignRole.OWNER,
        ]

    @pytest.mark.parametrize(
        ("role", "minimum", "expected"),
        [
            (CallsignRole.VIEWER, CallsignRole.VIEWER, True),
            (CallsignRole.VIEWER, CallsignRole.LOGGER, False),
            (CallsignRole.LOGGER, CallsignRole.VIEWER, True),
            (CallsignRole.LOGGER, CallsignRole.MANAGER, False),
            (CallsignRole.MANAGER, CallsignRole.LOGGER, True),
            (CallsignRole.MANAGER, CallsignRole.OWNER, False),
            (CallsignRole.OWNER, CallsignRole.MANAGER, True),
            (CallsignRole.OWNER, CallsignRole.OWNER, True),
            ("manager", "viewer", True),
            (None, CallsignRole.VIEWER, False),
        ],
    )
    def test_role_at_least(self, role, minimum, expected):
        assert role_at_least(role, minimum) is expected

    def test_roles_at_least(self):
        assert roles_at_least(CallsignRole.MANAGER) == [
            CallsignRole.MANAGER,
            CallsignRole.OWNER,
        ]
        assert roles_at_least(CallsignRole.VIEWER) == list(CallsignRole)
