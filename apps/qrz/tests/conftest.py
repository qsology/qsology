import pytest
from apps.qrz.models import QRZCredential

SESSION_KEY = "2331uf894c4bd29f3923f3bacf02c532d7bd9"


def qrz_xml(session: str = "", callsign: str = "") -> bytes:
    """A QRZ XML response, built like the real ones"""
    return (
        '<?xml version="1.0" encoding="utf-8" ?>\n'
        '<QRZDatabase version="1.34" xmlns="http://xmldata.qrz.com">\n'
        f"{callsign}"
        f"<Session>{session}<GMTime>Sun Oct 20 12:00:00 2026</GMTime>"
        "</Session>\n"
        "</QRZDatabase>\n"
    ).encode()


def login_ok(key: str = SESSION_KEY) -> bytes:
    return qrz_xml(
        f"<Key>{key}</Key><Count>123</Count>"
        "<SubExp>Wed Jan 1 12:34:03 2027</SubExp>"
    )


def login_failed() -> bytes:
    return qrz_xml("<Error>Username/password incorrect</Error>")


def expired() -> bytes:
    return qrz_xml("<Error>Session Timeout</Error>")


def not_found(call: str = "XX9XXX") -> bytes:
    return qrz_xml(f"<Key>{SESSION_KEY}</Key><Error>Not found: {call}</Error>")


AA7BQ = """<Callsign>
  <call>AA7BQ</call>
  <aliases>N6UFT,KJ6RK,DL/AA7BQ</aliases>
  <dxcc>291</dxcc>
  <fname>FRED L</fname>
  <name>LLOYD</name>
  <addr1>8711 E PINNACLE PEAK RD 159</addr1>
  <addr2>SCOTTSDALE</addr2>
  <state>AZ</state>
  <zip>85014</zip>
  <country>United States</country>
  <lat>34.23456</lat>
  <lon>-112.34356</lon>
  <grid>DM32af</grid>
  <county>Maricopa</county>
  <land>United States</land>
  <email>flloyd@qrz.com</email>
  <qslmgr>none</qslmgr>
  <cqzone>3</cqzone>
  <ituzone>2</ituzone>
  <iota>NA-001</iota>
  <name_fmt>FRED "The Boss" LLOYD</name_fmt>
</Callsign>
"""


def found(callsign: str = AA7BQ, message: str = "") -> bytes:
    extra = f"<Message>{message}</Message>" if message else ""
    return qrz_xml(f"<Key>{SESSION_KEY}</Key>{extra}", callsign)


@pytest.fixture
def credential(user):
    return QRZCredential.objects.create(
        user=user, username="m0wme", password="hunter2"
    )
