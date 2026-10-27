"""What's known about a callsign while it's being logged: the QSOs with it
before, and QRZ.com if the user has a login there"""

import json
import logging
from dataclasses import dataclass, field

from apps.logbook.models import QSO
from apps.qrz import services as qrz
from apps.qrz.client import QRZResult
from apps.qrz.models import QRZCredential

logger = logging.getLogger(__name__)

# What a look up fills into the form, if they're empty
FILLS = ("name", "qth", "gridsquare", "country", "state")
PREVIOUS = 3


@dataclass
class Lookup:
    call: str
    # Fields to fill in, if the form has them empty
    fill: dict[str, str] = field(default_factory=dict)
    count: int = 0
    previous: list[QSO] = field(default_factory=list)
    qrz: QRZResult | None = None
    qrz_message: str = ""

    @property
    def fill_json(self) -> str:
        return json.dumps(self.fill)


def lookup(user, call: str, fetch=None) -> Lookup:
    """Look up a callsign. QRZ.com is only asked if the user has a login
    there, and its answer wins over the last QSO's"""
    result = Lookup(call="".join(call.split()).upper())
    if not result.call:
        return result
    previous = QSO.objects.filter(owner=user, call=result.call).order_by(
        "-time_on", "-id"
    )
    result.count = previous.count()
    result.previous = list(previous[:PREVIOUS])
    if result.previous:
        last = result.previous[0]
        for name in FILLS:
            if getattr(last, name):
                result.fill[name] = getattr(last, name)
    if not QRZCredential.objects.filter(user=user, is_enabled=True).exists():
        return result
    try:
        found = qrz.lookup(user, result.call, fetch=fetch)
    except qrz.QRZNotFound:
        result.qrz_message = f"QRZ.com doesn't know {result.call}"
    except qrz.QRZError as exc:
        logger.info("QRZ lookup of %s failed: %s", result.call, exc)
        result.qrz_message = "QRZ.com couldn't be reached"
    else:
        result.qrz = found
        for name, value in found.qso_fields().items():
            if name in FILLS and value not in (None, ""):
                result.fill[name] = str(value)
    return result
