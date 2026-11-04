"""Normalising part descriptions so that "4k7", "4.7kΩ" and "4700" compare equal.

Used by the inventory matcher and the schematic-Markdown importer. Everything
here is deterministic string handling; nothing is looked up online.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

# --- categories ---------------------------------------------------------------

CATEGORY_LABELS = {
    "resistor": "저항",
    "capacitor": "콘덴서",
    "capacitor_polarized": "극성 콘덴서",
    "inductor": "인덕터",
    "led": "LED",
    "diode": "다이오드",
    "fuse": "퓨즈",
    "connector": "커넥터",
    "transistor": "트랜지스터",
    "ic": "IC",
    "crystal": "크리스탈",
    "switch": "스위치",
}

# Checked in order; the first keyword contained in the text wins, so specific words come first.
_CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("capacitor_polarized", ("전해", "탄탈", "극성콘덴서", "극성커패시터", "electrolytic", "tantalum", "polarized",
                             "polarised", "c_polarized")),
    ("led", ("led", "발광")),
    ("diode", ("다이오드", "diode", "zener", "제너", "schottky", "쇼트키", "tvs")),
    ("resistor", ("저항", "resistor", "레지스터")),
    ("capacitor", ("콘덴서", "커패시터", "캐패시터", "capacitor", "mlcc", "condenser")),
    ("inductor", ("인덕터", "inductor", "코일", "coil", "ferrite", "페라이트")),
    ("fuse", ("퓨즈", "fuse", "polyfuse")),
    ("connector", ("커넥터", "connector", "header", "헤더", "terminal", "단자", "socket", "소켓", "jack", "잭")),
    ("transistor", ("트랜지스터", "transistor", "mosfet", "fet", "bjt")),
    ("crystal", ("크리스탈", "크리스털", "crystal", "resonator", "오실레이터", "oscillator")),
    ("switch", ("스위치", "switch", "버튼", "button", "tact")),
    ("ic", ("ic", "mcu", "레귤레이터", "regulator", "ldo", "opamp", "op-amp", "op amp", "컨트롤러", "controller",
            "transceiver", "트랜시버", "드라이버", "driver", "메모리", "memory", "센서", "sensor")),
]

_CATEGORY_SHORT = {"r": "resistor", "c": "capacitor", "l": "inductor", "d": "diode", "u": "ic", "q": "transistor",
                   "j": "connector", "f": "fuse", "y": "crystal", "sw": "switch"}


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip().lower()


def category_from_text(text: str | None) -> str | None:
    """'저항', 'Resistor', 'MLCC 0603' ... -> canonical category key."""
    if not text:
        return None
    folded = _fold(text)
    if folded in CATEGORY_LABELS:
        return folded
    if folded in _CATEGORY_SHORT:
        return _CATEGORY_SHORT[folded]
    compact = folded.replace(" ", "")
    tokens = set(re.findall(r"[a-z0-9]+", folded))
    for key, words in _CATEGORY_KEYWORDS:
        for w in words:
            # Short Latin words (ic, led, fet) must be whole words; anything else may be part of a word.
            if (w in tokens) if (w.isascii() and len(w) <= 3) else (w.replace(" ", "") in compact):
                return key
    return None


_SYMBOL_CATEGORIES = [
    (re.compile(r"^Device:(C_Polarized|CP)"), "capacitor_polarized"),
    (re.compile(r"^Device:LED"), "led"),
    (re.compile(r"^Device:D(_|$)"), "diode"),
    (re.compile(r"^Device:R(_|$)"), "resistor"),
    (re.compile(r"^Device:C(_|$)"), "capacitor"),
    (re.compile(r"^Device:L(_|$)"), "inductor"),
    (re.compile(r"^Device:(Fuse|Polyfuse)"), "fuse"),
    (re.compile(r"^Device:(Crystal|Resonator)"), "crystal"),
    (re.compile(r"^Device:Q_"), "transistor"),
    (re.compile(r"^Connector"), "connector"),
    (re.compile(r"^Switch:"), "switch"),
]

_REF_PREFIX = [("LED", "led"), ("SW", "switch"), ("CN", "connector"), ("R", "resistor"), ("C", "capacitor"),
               ("L", "inductor"), ("D", "diode"), ("J", "connector"), ("P", "connector"), ("F", "fuse"),
               ("Q", "transistor"), ("U", "ic"), ("Y", "crystal"), ("X", "crystal")]


def category_of(symbol_id: str | None, reference: str | None = None) -> str | None:
    """Category of an IR component: from its KiCad symbol first, then its reference prefix."""
    if symbol_id:
        for pattern, key in _SYMBOL_CATEGORIES:
            if pattern.search(symbol_id):
                return key
    if reference:
        prefix = re.match(r"^[A-Z]+", reference)
        if prefix:
            for p, key in _REF_PREFIX:
                if prefix.group() == p:
                    return key
    return None


# --- values -------------------------------------------------------------------------

_SI = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3, "": 1.0, "R": 1.0, "r": 1.0,
       "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9}
_UNIT_SUFFIX = re.compile(r"(?:ohms?|Ω|ω|F|H)$", re.IGNORECASE)
_VALUE = re.compile(r"(\d+(?:\.\d+)?)([pnuµμmkKMGRr]?)(\d*)")


def parse_value(text: str | None) -> float | None:
    """'4k7' -> 4700.0, '0.1uF' -> 1e-07, '330R' -> 330.0, '100n' -> 1e-07; None if not a plain value.

    Only the leading value is read, so '10k 1%' and '10k/0.1W' give 10000.0.
    """
    if not text:
        return None
    s = unicodedata.normalize("NFKC", text).strip().replace(",", ".")
    s = re.sub(r"(\d)\s+(?=[pnuµμmkKMGRrΩω]|ohm|F\b|H\b)", r"\1", s)  # "4.7 kΩ" -> "4.7kΩ"
    s = re.split(r"[\s/;@]", s, maxsplit=1)[0]
    s = _UNIT_SUFFIX.sub("", s)
    m = _VALUE.fullmatch(s)
    if not m:
        return None
    whole, prefix, frac = m.groups()
    if frac and "." in whole:
        return None
    number = float(f"{whole}.{frac}" if frac else whole)
    return number * _SI[prefix]


def values_equal(a: str | None, b: str | None) -> bool:
    va, vb = parse_value(a), parse_value(b)
    if va is not None and vb is not None:
        return math.isclose(va, vb, rel_tol=1e-9, abs_tol=1e-15)
    if a is None or b is None:
        return False
    return _fold(a).replace(" ", "") == _fold(b).replace(" ", "")


# --- packages -----------------------------------------------------------------------

_CHIP = re.compile(r"(?<!\d)(01005|0201|0402|0603|0805|1206|1210|1812|2010|2512)(?!\d)")
_IC = re.compile(r"(SOT-?23(?:-\d+)?|SOT-?223|SOT-?89|SOIC-?\d+|SOP-?\d+|TSSOP-?\d+|MSOP-?\d+|SSOP-?\d+|"
                 r"[DVUW]?QFN-?\d+|LQFP-?\d+|TQFP-?\d+|TO-?220|TO-?92|TO-?252|DPAK|D2PAK|SOD-?123F?|SOD-?323|"
                 r"SOD-?523|SMA|SMB|SMC|DIP-?\d+|MELF|MINIMELF)(?![A-Za-z0-9])", re.IGNORECASE)
_GRID = re.compile(r"(?<!\d)(\d{1,2})x(\d{1,2})(?!\d)", re.IGNORECASE)
_PITCH = re.compile(r"P?(\d+(?:\.\d+)?)\s?mm", re.IGNORECASE)


@dataclass(frozen=True)
class PackageKey:
    size: str | None
    """'0603', 'SOT23-5', '1x02' ...: the part that must be equal."""
    pitch: float | None
    raw: str

    @property
    def known(self) -> bool:
        return self.size is not None


def package_key(text: str | None) -> PackageKey | None:
    """Pull the comparable package out of a footprint id or a free-text package column."""
    if not text:
        return None
    folded = unicodedata.normalize("NFKC", text)
    size = None
    if m := _CHIP.search(folded):
        size = m.group(1)
    elif m := _IC.search(folded):
        size = m.group(1).upper().replace("-", "")
    elif m := _GRID.search(folded):
        size = f"{int(m.group(1))}x{int(m.group(2)):02d}"
    pitch = float(p.group(1)) if (p := _PITCH.search(folded)) else None
    raw = re.sub(r"[\s_\-]", "", folded.lower())
    return PackageKey(size, pitch, raw)


def packages_match(a: PackageKey | None, b: PackageKey | None) -> bool | None:
    """True / False when both sides say enough; None when one side is unknown."""
    if a is None or b is None:
        return None
    if a.known and b.known:
        if a.size != b.size:
            return False
        if a.pitch is not None and b.pitch is not None and not math.isclose(a.pitch, b.pitch, abs_tol=0.01):
            return False
        return True
    if not a.known and not b.known:
        return True if a.raw == b.raw else None
    return None


def normalize_mpn(mpn: str | None) -> str | None:
    if not mpn:
        return None
    s = re.sub(r"[\s\-_./]", "", unicodedata.normalize("NFKC", mpn).upper())
    return s or None


# --- standard chip footprints -----------------------------------------------------------------

# Checked to exist in the KiCad 10.0.8 footprint libraries.
_CHIP_FOOTPRINT_LIBS = {"resistor": ("Resistor_SMD", "R"), "capacitor": ("Capacitor_SMD", "C"),
                        "inductor": ("Inductor_SMD", "L"), "led": ("LED_SMD", "LED")}
_CHIP_METRIC = {"0402": "1005", "0603": "1608", "0805": "2012", "1206": "3216"}


def chip_footprint(category: str | None, package: str | None) -> str | None:
    """'resistor', '0603' -> 'Resistor_SMD:R_0603_1608Metric'; None when not a standard chip size."""
    m = re.fullmatch(r"(?:\D*?)(0402|0603|0805|1206)(?:\D.*)?", package or "")
    lib = _CHIP_FOOTPRINT_LIBS.get(category or "")
    if not m or not lib:
        return None
    size = m.group(1)
    return f"{lib[0]}:{lib[1]}_{size}_{_CHIP_METRIC[size]}Metric"
