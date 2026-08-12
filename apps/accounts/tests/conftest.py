import pytest
from django.contrib.auth import get_user_model


@pytest.fixture
def make_user(db):
    """Factory for creating users with unique usernames"""
    User = get_user_model()
    counter = 0

    def _make_user(**kwargs):
        nonlocal counter
        counter += 1
        kwargs.setdefault("username", f"user{counter}")
        kwargs.setdefault("email", f"user{counter}@qsology.test")
        kwargs.setdefault("password", "correct-horse-battery-staple")
        return User.objects.create_user(**kwargs)

    return _make_user


@pytest.fixture
def user(make_user):
    return make_user(
        username="m0wme", display_name="m0wme", email="m0wme@qsology.test"
    )
