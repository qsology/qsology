import gzip
import json
from decimal import Decimal
from pathlib import Path

import pytest
from apps.locations.models import DxccEntity, Reference, ReferenceProgram

DATA = Path(__file__).parent / "data"

# Cut down from the real IOTA fulllist.json. AF-001 has its latitudes the
# wrong way round as the real list does, OC-999 crosses the antimeridian
IOTA_SAMPLE = [
    {
        "refno": "AF-001",
        "name": "Agalega Islands",
        "dxcc_num": "4",
        "latitude_max": "-10.75",
        "latitude_min": "-10.00",
        "longitude_max": "57.00",
        "longitude_min": "56.25",
        "grp_region": "",
        "whitelist": "0",
        "pc_credited": "37.2",
        "comment": "",
        "sub_groups": [
            {
                "subref": "AF-001-0-0",
                "subname": "[No sub-group]",
                "status": "Active",
                "islands": [
                    {
                        "id": "493",
                        "island_name": "North",
                        "comment": "",
                        "excluded": "0",
                    }
                ],
            }
        ],
    },
    {
        "refno": "eu-005",
        "name": "Great Britain",
        "dxcc_num": "223",
        "latitude_max": "58.75",
        "latitude_min": "49.75",
        "longitude_max": "2.00",
        "longitude_min": "-6.50",
        "grp_region": "",
        "whitelist": "0",
        "pc_credited": "90.0",
        "comment": "",
        "sub_groups": [],
    },
    {
        "refno": "OC-999",
        "name": "Dateline Islands",
        "dxcc_num": "",
        "latitude_max": "-17.00",
        "latitude_min": "-16.00",
        "longitude_max": "-179.00",
        "longitude_min": "179.00",
        "grp_region": "",
        "whitelist": "0",
        "pc_credited": "0",
        "comment": "",
        "sub_groups": [
            {
                "subref": "OC-999-0-0",
                "subname": "[No sub-group]",
                "status": "Deleted",
                "islands": [],
            }
        ],
    },
]

# Cut down from the TQSL config.tq6 published for LoTW
TQSL_SAMPLE = """<?xml version="1.0" encoding="UTF-8" ?>
<tqsldata>
  <tqslconfig majorversion="11" minorversion="34">
    <dxcc>
      <entity arrlId="0" deleted="0" valid="1912-12-13 00:00:00">-NONE-</entity>
      <entity arrlId="1" deleted="0" valid="1945-11-15 00:00:00" zonemap="02:01,02:03,03:01,09:05">CANADA</entity>
      <entity arrlId="2" deleted="1" valid="1945-11-15 00:00:00" invalid="1991-03-31 00:00:00" zonemap="39:21">ABU AIL ISLANDS (DELETED)</entity>
      <entity arrlId="223" deleted="0" valid="1945-11-15 00:00:00" zonemap="27:14">ENGLAND</entity>
      <entity arrlId="291" deleted="0" valid="1945-11-15 00:00:00" zonemap="08:05,08:04,07:04">UNITED STATES OF AMERICA</entity>
    </dxcc>
  </tqslconfig>
</tqsldata>
"""  # noqa: E501


@pytest.fixture
def pota_csv() -> bytes:
    return (DATA / "pota_sample.csv").read_bytes()


@pytest.fixture
def sota_csv() -> bytes:
    return (DATA / "sota_sample.csv").read_bytes()


@pytest.fixture
def iota_json() -> bytes:
    return json.dumps(IOTA_SAMPLE).encode()


@pytest.fixture
def tqsl_xml() -> bytes:
    return TQSL_SAMPLE.encode()


@pytest.fixture
def tqsl_gz(tqsl_xml) -> bytes:
    return gzip.compress(tqsl_xml)


@pytest.fixture
def england(db):
    return DxccEntity.objects.create(
        code=223, name="ENGLAND", cq_zones=[14], itu_zones=[27]
    )


@pytest.fixture
def usa(db):
    return DxccEntity.objects.create(
        code=291,
        name="UNITED STATES OF AMERICA",
        cq_zones=[3, 4, 5],
        itu_zones=[6, 7, 8],
    )


@pytest.fixture
def park(db):
    return Reference.objects.create(
        program=ReferenceProgram.POTA,
        code="GB-0001",
        name="Lake District National Park",
        dxcc_code=223,
        region="GB-ENG",
        latitude=Decimal("54.5"),
        longitude=Decimal("-3.1"),
        gridsquare="IO84lm",
    )


@pytest.fixture
def summit(db):
    return Reference.objects.create(
        program=ReferenceProgram.SOTA,
        code="G/LD-001",
        name="Scafell Pike",
        latitude=Decimal("54.454222"),
        longitude=Decimal("-3.211528"),
        gridsquare="IO84jk",
        altitude_m=978,
    )


class FakeFetch:
    """Stands in for apps.common.http.get, returning canned responses"""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, params=None, timeout=30):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def fake_fetch():
    return FakeFetch
