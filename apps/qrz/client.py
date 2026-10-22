"""QRZ.com XML callsign lookups

See https://www.qrz.com/docs/xml/current_spec.html. Logging in gives a session
key which is used for lookups until QRZ says it has expired, at which point
the client logs in again once and retries"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from apps.common import http
from django.conf import settings

NAMESPACE = "{http://xmldata.qrz.com}"
SESSION_EXPIRED = ("session timeout", "invalid session key")


class QRZError(Exception):
    """QRZ couldn't be reached or returned an error"""


class QRZAuthError(QRZError):
    """The username or password was refused"""


class QRZNotFound(QRZError):
    """QRZ has no record of the callsign"""


@dataclass
class QRZResult:
    """A callsign record from QRZ, raw holds every field as QRZ sent it"""

    callsign: str
    raw: dict[str, str] = field(default_factory=dict)
    message: str = ""

    def _decimal(self, name: str) -> Decimal | None:
        try:
            return Decimal(self.raw[name])
        except KeyError, InvalidOperation:
            return None

    def _int(self, name: str) -> int | None:
        try:
            return int(self.raw[name])
        except KeyError, ValueError:
            return None

    @property
    def name(self) -> str:
        if self.raw.get("name_fmt"):
            return self.raw["name_fmt"]
        first = self.raw.get("nickname") or self.raw.get("fname", "")
        return " ".join(filter(None, (first, self.raw.get("name", ""))))

    def qso_fields(self) -> dict:
        """The record as QSO field values, empty values are left out"""
        fields = {
            "name": self.name,
            "qth": self.raw.get("addr2", ""),
            "state": self.raw.get("state", ""),
            "cnty": self.raw.get("county", ""),
            "country": self.raw.get("country", ""),
            "gridsquare": self.raw.get("grid", ""),
            "lat": self._decimal("lat"),
            "lon": self._decimal("lon"),
            "dxcc": self._int("dxcc"),
            "cqz": self._int("cqzone"),
            "ituz": self._int("ituzone"),
            "iota": self.raw.get("iota", ""),
            "email": self.raw.get("email", ""),
            "qsl_via": self.raw.get("qslmgr", ""),
        }
        return {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }


def _children(element) -> dict[str, str]:
    if element is None:
        return {}
    return {
        child.tag.removeprefix(NAMESPACE): (child.text or "").strip()
        for child in element
    }


class QRZClient:
    def __init__(
        self,
        username: str,
        password: str,
        session_key: str = "",
        fetch: http.Fetch | None = None,
    ):
        self.username = username
        self.password = password
        self.session_key = session_key
        self.fetch = fetch or http.get

    def _request(self, params: dict) -> tuple[dict, dict]:
        """Make a request, returning the session and callsign sections"""
        try:
            body = self.fetch(settings.QRZ_XML_URL, params=params, timeout=15)
            root = ET.fromstring(body)
        except http.HTTPError as exc:
            raise QRZError(str(exc)) from exc
        except ET.ParseError as exc:
            raise QRZError("QRZ sent back invalid XML") from exc
        session = _children(root.find(f"{NAMESPACE}Session"))
        callsign = _children(root.find(f"{NAMESPACE}Callsign"))
        return session, callsign

    def login(self) -> str:
        session, _ = self._request(
            {
                "username": self.username,
                "password": self.password,
                "agent": settings.QRZ_AGENT,
            }
        )
        if not session.get("Key"):
            error = session.get("Error") or "QRZ login failed"
            raise QRZAuthError(error)
        self.session_key = session["Key"]
        return self.session_key

    def lookup(self, callsign: str) -> QRZResult:
        callsign = callsign.strip().upper()
        if not callsign:
            raise QRZNotFound("No callsign given")
        for attempt in range(2):
            if not self.session_key:
                self.login()
            session, record = self._request(
                {"s": self.session_key, "callsign": callsign}
            )
            error = session.get("Error", "")
            if error.lower().startswith(SESSION_EXPIRED) and attempt == 0:
                self.session_key = ""
                continue
            if record:
                return QRZResult(
                    callsign=record.get("call", callsign),
                    raw=record,
                    message=session.get("Message", ""),
                )
            if error.lower().startswith("not found"):
                raise QRZNotFound(error)
            raise QRZError(error or "QRZ returned no record")
        raise QRZError("QRZ session could not be renewed")
