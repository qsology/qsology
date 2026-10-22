"""The ways a notification can be sent

Only logging exists for now. Email, push and the like are added by
subclassing Channel and adding it to settings.NOTIFICATION_CHANNELS"""

import functools
import logging

from django.conf import settings
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)


class Channel:
    """A way of sending notifications"""

    name = ""

    def send(self, notification) -> None:
        """Send the notification, raising if it couldn't be sent"""
        raise NotImplementedError


class LogChannel(Channel):
    """Writes notifications to the log, a stand in until users can choose
    how they're told"""

    name = "log"

    def send(self, notification) -> None:
        logger.info(
            "Notification %s for user %s: %s",
            notification.kind,
            notification.user_id,
            notification.title,
        )


@functools.cache
def _load(path: str) -> Channel:
    return import_string(path)()


def get_channel(name: str) -> Channel:
    """Raises KeyError for a channel that isn't set up"""
    return _load(settings.NOTIFICATION_CHANNELS[name])


def preferred_channels(user, kind: str) -> list[str]:
    """The channels the user wants this kind of notification sent through

    TODO: users can't choose yet, so everyone gets the defaults"""
    return list(settings.NOTIFICATION_DEFAULT_CHANNELS)
