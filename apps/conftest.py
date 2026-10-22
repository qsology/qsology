import os

import pytest
from django.contrib.auth import get_user_model
from hypothesis import settings

# Property based tests run a quick set of examples by default, set
# HYPOTHESIS_PROFILE=thorough (task test:thorough) for a much longer run
settings.register_profile(
    "thorough", max_examples=1000, stateful_step_count=200
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))


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


@pytest.fixture
def other_user(make_user):
    return make_user(
        username="k4gbi", display_name="k4gbi", email="k4gbi@qsology.test"
    )
