"""Datasheet facts and the small, deterministic helpers that use them.

A datasheet is never trusted as a whole. What the app uses is a *summary* ("데이터시트 요약 MD",
docs/prompts/datasheet-summary.md) that a person, Copilot or the optional AI wrote from the PDF,
with a page number on every row. The checks in :mod:`circuit_core.datasheet_checks` compare that
summary with the circuit; the PDF stays the reference a person goes back to.

This module also decides whether a downloaded PDF is plausibly the right document
(:func:`identity_check`): the part number, or at least its family, must appear in the text.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, Field

from .models import Component

GENERIC_SYMBOL_LIBS = frozenset({
    "Device", "Connector", "Connector_Generic", "Connector_Generic_MountingPin", "Connector_Generic_Shielded",
    "Jumper", "Mechanical", "power", "Switch", "Graphic", "TestPoint", "Unknown",
})
"""Symbol libraries whose symbols stand for any maker's part (a resistor, a header)."""


# --- facts (parsed from the summary Markdown) ----------------------------------------------------

class DsPin(BaseModel):
    package: str
    number: str
    name: str
    kind: str | None = None
    description: str | None = None
    page: int | None = None


class DsRating(BaseModel):
    parameter: str
    pins: list[str] = Field(default_factory=list)
    min: float | None = None
    typ: float | None = None
    max: float | None = None
    unit: str = ""
    condition: str | None = None
    page: int | None = None


class DsExternalPart(BaseModel):
    requirement: Literal["required", "recommended"]
    kind: str | None = Field(default=None, description="Category key from circuit_core.parts, if recognised.")
    kind_label: str
    value: str | None = None
    quantity: int | None = None
    endpoints: list[str] = Field(default_factory=list, description="Two pin names (or GND) the part connects.")
    description: str | None = None
    page: int | None = None


class DsNote(BaseModel):
    text: str
    page: int | None = None


class DatasheetFacts(BaseModel):
    format: str
    manufacturer: str | None = None
    part_numbers: list[str] = Field(default_factory=list)
    document: str | None = None
    source_file: str | None = None
    page_count: int | None = None
    pins: list[DsPin] = Field(default_factory=list)
    abs_max: list[DsRating] = Field(default_factory=list)
    recommended: list[DsRating] = Field(default_factory=list)
    external_parts: list[DsExternalPart] = Field(default_factory=list)
    notes: list[DsNote] = Field(default_factory=list, description="Application-circuit notes, for design work.")
    uncertain: list[dict] = Field(default_factory=list)

    def packages(self) -> list[str]:
        seen: list[str] = []
        for p in self.pins:
            if p.package not in seen:
                seen.append(p.package)
        return seen

    def pins_of(self, package: str) -> list[DsPin]:
        return [p for p in self.pins if p.package == package]


# --- part identity -----------------------------------------------------------------------------

def normalize_part(text: str | None) -> str:
    """Upper-case letters and digits only: 'SN65HVD230-DR' -> 'SN65HVD230DR'."""
    if not text:
        return ""
    return re.sub(r"[^A-Z0-9]", "", unicodedata.normalize("NFKC", text).upper())


def part_core(text: str | None) -> str:
    """The shortest prefix that still names a device family:
    'STM32F103C8T6' -> 'STM32F103', 'LM1117MPX-3.3' -> 'LM1117', 'MCP2551-I/SN' -> 'MCP2551'."""
    if not text:
        return ""
    head = re.split(r"[-/ ,(]", unicodedata.normalize("NFKC", text).upper().strip(), maxsplit=1)[0]
    head = re.sub(r"[^A-Z0-9]", "", head)
    m = re.match(r"^[A-Z]*\d+(?:[A-Z]+\d+)?", head)
    return m.group() if m else head


def symbol_part_name(symbol_id: str | None) -> str | None:
    """'Interface_CAN_LIN:SN65HVD230' -> 'SN65HVD230' for vendor-specific libraries, else None."""
    if not symbol_id or ":" not in symbol_id:
        return None
    lib, name = symbol_id.split(":", 1)
    return None if lib in GENERIC_SYMBOL_LIBS else name


def identity(component: Component, symbol_datasheet_url: str | None = None) -> str:
    """How sure we are which exact part this is.

    ``mpn``     the maker and the maker's part number are recorded;
    ``symbol``  the KiCad symbol is a specific part (e.g. ``Interface_CAN_LIN:SN65HVD230``);
    ``generic`` neither: a generic resistor/connector or a part still to be chosen.
    """
    if component.manufacturer and component.manufacturer_part_number:
        return "mpn"
    if symbol_part_name(component.symbol_id) and (symbol_datasheet_url or component.manufacturer_part_number):
        return "symbol"
    return "generic"


def part_candidates(component: Component) -> list[str]:
    """Names to look for in a datasheet: the MPN and the specific symbol name."""
    out = []
    for name in (component.manufacturer_part_number, symbol_part_name(component.symbol_id)):
        if name and name not in out:
            out.append(name)
    return out


def identity_check(text: str, candidates: list[str]) -> dict:
    """Does the PDF text name this part?

    ``matched``   a full candidate (MPN or symbol name) appears;
    ``family``    only a family prefix appears (e.g. 'STM32F103C8' for 'STM32F103C8T6');
    ``not_found`` the text names none of them — maybe the wrong document;
    ``no_text``   no text could be read (scanned PDF or unsupported encryption).
    """
    haystack = normalize_part(text)
    checked = [c for c in candidates if normalize_part(c)]
    if not haystack:
        return {"status": "no_text", "found": [], "checked": checked}
    found: list[str] = []
    status = "not_found"
    for candidate in checked:
        full = normalize_part(candidate)
        if full in haystack:
            if candidate not in found:
                found.append(candidate)
            status = "matched"
            continue
        core = part_core(candidate)
        for end in range(len(full) - 1, max(len(core), 4) - 1, -1):
            prefix = full[:end]
            if prefix.startswith(core) and prefix in haystack:
                if prefix not in found:
                    found.append(prefix)
                if status == "not_found":
                    status = "family"
                break
    return {"status": status, "found": found, "checked": checked}


# --- names and voltages --------------------------------------------------------------------------

_GROUND_NAMES = {"GND", "VSS", "GROUND", "0V", "AGND", "DGND", "PGND", "GNDA", "GNDD", "GNDPWR", "SGND", "VSSA"}
_PAD_NAMES = {"EP", "EPAD", "PAD", "THERMALPAD", "EXPOSEDPAD", "TAB"}


def norm_pin_name(name: str | None) -> str:
    """Comparable pin name: 'VDD_1' -> 'VDD', '~{RESET}' -> 'RESET', 'N.C.' -> 'NC'."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", name).upper().replace("~{", "").replace("}", "").strip()
    s = re.sub(r"[_\s]\d+$", "", s)
    s = re.sub(r"[^A-Z0-9/+\-]", "", s)
    if s in _GROUND_NAMES:
        return "GND"
    if s in _PAD_NAMES:
        return "EP"
    return s


# Names that datasheets and KiCad symbols use for the same regulator / supply pin (LM1117: VIN = VI, VOUT = VO).
_PIN_ALIASES = {"VI": "VIN", "IN": "VIN", "INPUT": "VIN", "VO": "VOUT", "OUT": "VOUT", "OUTPUT": "VOUT"}


def _alias(name: str) -> str:
    return _PIN_ALIASES.get(name, name)


def pin_names_match(a: str | None, b: str | None) -> bool:
    """True if two pin names mean the same pin: alternate functions ('PA0-WKUP/ADC_IN0'), names listing several
    roles ('ADJ/GND' is the GND pin of a fixed regulator) and common aliases (VIN = VI = IN, VOUT = VO = OUT)."""
    x, y = norm_pin_name(a), norm_pin_name(b)
    if not x or not y:
        return False
    if _alias(x) == _alias(y):
        return True
    first = lambda s: re.split(r"[/\-+]", s, maxsplit=1)[0]  # noqa: E731
    if first(x) and _alias(first(x)) == _alias(first(y)):
        return True
    roles = lambda s: {_alias(norm_pin_name(r)) for r in s.split("/") if norm_pin_name(r)}  # noqa: E731
    return bool(roles(x) & roles(y)) and ("/" in x or "/" in y)


def is_ground_name(name: str | None) -> bool:
    return norm_pin_name(name) == "GND" or bool(name and re.fullmatch(r"(?i)gnd\w*|\w*_gnd", name))


def net_voltage(name: str) -> float | None:
    """Nominal voltage from a net name: '+3V3' -> 3.3, '+5V' -> 5, 'VCC_12V' -> 12, '-5V' -> -5,
    'GND' -> 0. Names without a voltage (VCC, VDD, VBAT) give None."""
    upper = name.upper()
    if is_ground_name(upper):
        return 0.0
    m = re.search(r"(?:^|[_+\-])([+\-]?)(\d+)V(\d+)?(?=$|_)", upper)
    if not m:
        m = re.fullmatch(r"([+\-]?)(\d+)V(\d+)?", upper)
    if not m:
        return None
    sign = -1.0 if m.group(1) == "-" or upper.startswith("-") else 1.0
    whole, frac = m.group(2), m.group(3)
    return sign * float(f"{whole}.{frac}" if frac else whole)


def net_voltages(circuit) -> dict[str, float | None]:
    """Voltage of every net of a circuit: its own ``Net.voltage`` when set, else read from the name."""
    return {n.name: n.voltage if n.voltage is not None else net_voltage(n.name) for n in circuit.nets}


def to_volts(value: float | None, unit: str) -> float | None:
    if value is None:
        return None
    u = unit.strip().replace(" ", "")
    if u in ("V", "v"):
        return value
    if u in ("mV", "mv"):
        return value / 1000
    return None
