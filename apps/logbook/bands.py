"""Bands and modes from the ADIF specification"""

import string
from decimal import Decimal
from typing import NamedTuple

_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


class Band(NamedTuple):
    name: str
    low: Decimal
    high: Decimal

    def contains(self, freq: Decimal) -> bool:
        return self.low <= freq <= self.high


# ADIF band enumeration, frequencies in MHz
BANDS = tuple(
    Band(name, Decimal(low), Decimal(high))
    for name, low, high in (
        ("2190m", ".1357", ".1378"),
        ("630m", ".472", ".479"),
        ("560m", ".501", ".504"),
        ("160m", "1.8", "2.0"),
        ("80m", "3.5", "4.0"),
        ("60m", "5.06", "5.45"),
        ("40m", "7.0", "7.3"),
        ("30m", "10.1", "10.15"),
        ("20m", "14.0", "14.35"),
        ("17m", "18.068", "18.168"),
        ("15m", "21.0", "21.45"),
        ("12m", "24.890", "24.99"),
        ("10m", "28.0", "29.7"),
        ("8m", "40", "45"),
        ("6m", "50", "54"),
        ("5m", "54.000001", "69.9"),
        ("4m", "70", "71"),
        ("2m", "144", "148"),
        ("1.25m", "222", "225"),
        ("70cm", "420", "450"),
        ("33cm", "902", "928"),
        ("23cm", "1240", "1300"),
        ("13cm", "2300", "2450"),
        ("9cm", "3300", "3500"),
        ("6cm", "5650", "5925"),
        ("3cm", "10000", "10500"),
        ("1.25cm", "24000", "24250"),
        ("6mm", "47000", "47200"),
        ("4mm", "75500", "81000"),
        ("2.5mm", "119980", "123000"),
        ("2mm", "134000", "149000"),
        ("1mm", "241000", "250000"),
        ("submm", "300000", "7500000"),
    )
)
BANDS_BY_NAME = {band.name: band for band in BANDS}


def normalize_band(value: str) -> str:
    """ADIF bands are case insensitive, they're kept lower case

    Only A-Z are changed, other characters can change length when their
    case does"""
    return value.strip().translate(_ASCII_LOWER)


def band_for_freq(freq: Decimal | None) -> str | None:
    """The band a frequency in MHz is in, None if it isn't in one"""
    if freq is None:
        return None
    for band in BANDS:
        if band.contains(freq):
            return band.name
    return None


# ADIF mode enumeration, with the submodes belonging to each
MODES: dict[str, frozenset[str]] = {
    mode: frozenset(
        filter(None, (name.strip() for name in submodes.split(",")))
    )
    for mode, submodes in {
        "AM": "",
        "ARDOP": "",
        "ATV": "",
        "CHIP": "CHIP64, CHIP128",
        "CLO": "",
        "CONTESTI": "",
        "CW": "PCW",
        "DIGITALVOICE": "DMR, M17, C4FM, DSTAR, FREEDV",
        "DOMINO": (
            "DOM4, DOM5, DOM8, DOM-M, DOM11, DOM16, DOM22, DOM44, DOM88, "
            "DOMINOF, DOMINOEX"
        ),
        "DYNAMIC": "VARA_HF, VARA_FM_1200, VARA_FM_9600, VARA_SATELLITE",
        "FAX": "",
        "FM": "",
        "FSK441": "",
        "FT8": "",
        "HELL": (
            "HFSK, FMHELL, HELL80, HELLX5, HELLX9, FSKHELL, PSKHELL, SLOWHELL"
        ),
        "ISCAT": "ISCAT-A, ISCAT-B",
        "JT4": "JT4A, JT4B, JT4C, JT4D, JT4E, JT4F, JT4G",
        "JT9": (
            "JT9A, JT9B, JT9C, JT9D, JT9E, JT9F, JT9G, JT9H, JT9-1, JT9-2, "
            "JT9-5, JT9-10, JT9-30, JT9E FAST, JT9F FAST, JT9G FAST, JT9H FAST"
        ),
        "JT44": "",
        "JT65": "JT65A, JT65B, JT65C, JT65B2, JT65C2",
        "MFSK": (
            "FT2, FT4, JS8, Q65, FST4, JTMS, FST4W, MFSK4, MFSK8, MFSK11, "
            "MFSK16, MFSK22, MFSK31, MFSK32, MFSK64, FSQCALL, MFSK128, "
            "MFSK64L, MFSK128L"
        ),
        "MSK144": "",
        "MT63": "",
        "MTONE": "SCAMP_FAST, SCAMP_SLOW, SCAMP_VSLOW",
        "OLIVIA": (
            "OLIVIA 4/125, OLIVIA 4/250, OLIVIA 8/250, OLIVIA 8/500, OLIVIA "
            "16/500, OLIVIA 16/1000, OLIVIA 32/1000"
        ),
        "OPERA": "OPERA-QSO, OPERA-BEACON",
        "PAC": "PAC2, PAC3, PAC4",
        "PAX": "PAX2",
        "PKT": "",
        "PSK": (
            "FSK31, PSK10, PSK31, PSK63, SIM31, PSK125, PSK250, PSK500, "
            "PSK63F, QPSK31, QPSK63, 8PSK125, 8PSK250, 8PSK500, PSK1000, "
            "PSK125R, "
            "PSK250R, PSK500R, PSKAM10, PSKAM31, PSKAM50, QPSK125, QPSK250, "
            "QPSK500, 8PSK1000, 8PSK125F, 8PSK250F, 8PSK500F, PSK1000R, "
            "PSK250C6, PSK500C2, PSK500C4, PSK63RC4, PSK63RC5, PSK800C2, "
            "PSKFEC31, 8PSK1000F, 8PSK1200F, 8PSK125FL, 8PSK250FL, PSK1000C2, "
            "PSK125C12, PSK125RC4, PSK125RC5, PSK250RC2, PSK250RC3, PSK250RC5, "
            "PSK250RC6, PSK250RC7, PSK500RC2, PSK500RC3, PSK500RC4, PSK63RC10, "
            "PSK63RC20, PSK63RC32, PSK800RC2, PSK1000RC2, PSK125RC10, "
            "PSK125RC12, PSK125RC16"
        ),
        "PSK2K": "",
        "Q15": "",
        "QRA64": "QRA64A, QRA64B, QRA64C, QRA64D, QRA64E",
        "ROS": "ROS-HF, ROS-MF, ROS-EME",
        "RTTY": "ASCI",
        "RTTYM": "",
        "SSB": "LSB, USB",
        "SSTV": "",
        "T10": "",
        "THOR": (
            "THOR4, THOR5, THOR8, THOR-M, THOR11, THOR16, THOR22, THOR100, "
            "THOR25X4, THOR50X1, THOR50X2"
        ),
        "THRB": "THRBX, THRBX1, THRBX2, THRBX4, THROB1, THROB2, THROB4",
        "TOR": "GTOR, NAVTEX, SITORB, AMTORFEC",
        "V4": "",
        "VOI": "",
        "WINMOR": "",
        "WSPR": "",
    }.items()
}

SUBMODE_TO_MODE = {
    submode: mode for mode, submodes in MODES.items() for submode in submodes
}
