"""Where a datasheet can come from: candidate links for one component, without any network access.

A *candidate* is one place to get the PDF, with the URLs to try in order:

``kicad_symbol``  the link in the component's KiCad symbol (usually the family datasheet);
``maker_site``    a URL built from the part number with the manufacturer's own address pattern;
``mouser`` / ``digikey``  a link a distributor's search API returned (added by the backend).

The address patterns are the ones the KiCad 10.0.8 symbol library itself uses most for each maker
(e.g. 2,739 TI links are ``ti.com/lit/ds/symlink/<part>.pdf``, 3,114 ST links are
``st.com/resource/en/datasheet/<part>.pdf``). Part numbers are tried from the full number down to
the family core (``DRV8871DDAR`` ... ``DRV8871``), at most four tries. Measured against the KiCad
library, these rules rebuild the symbol's own link for about 55% of TI, 64% of ST, 53% of Diodes,
34% of Nexperia and 27% of onsemi symbols (ADR-005, 2026-11 addition); the rest fail with a clear
message and the other candidates or "PDF 올리기" remain.

Nothing here fetches anything: the backend downloads a candidate only after the user confirmed it,
and checks that the PDF names the part.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from .datasheets import normalize_part, part_core, symbol_part_name
from .models import Component

CandidateSource = Literal["kicad_symbol", "maker_site", "mouser", "digikey"]
MAX_TRIES = 4


class DatasheetCandidate(BaseModel):
    id: str
    source: CandidateSource
    label: str
    urls: list[str] = Field(description="Tried in order until one returns a PDF.")
    manufacturer: str | None = None
    part_number: str | None = Field(default=None, description="The part number this source names.")
    description: str | None = None
    product_url: str | None = Field(default=None, description="A page to open in a browser if the download fails.")
    status: str | None = Field(default=None, description="Lifecycle status a distributor reports (Active, Obsolete…).")


def candidate_id(source: str, urls: list[str]) -> str:
    return source[:2] + "-" + hashlib.sha1((source + "\n" + "\n".join(urls)).encode("utf-8")).hexdigest()[:10]


def make_candidate(source: CandidateSource, label: str, urls: list[str], **fields) -> DatasheetCandidate:
    return DatasheetCandidate(id=candidate_id(source, urls), source=source, label=label, urls=urls, **fields)


# --- manufacturers -------------------------------------------------------------------------------

@dataclass(frozen=True)
class Maker:
    key: str
    name: str
    aliases: tuple[str, ...]
    hosts: tuple[str, ...]
    url: Callable[[str], str]
    core_first: bool = False
    """Try the family core first (TI publishes one datasheet per generic part number)."""
    voltage_suffix: bool = False
    """Also try the core without a two-digit output-voltage code (TI: TPS7A0233 -> TPS7A02, TLV70033 -> TLV700)."""


MAKERS: tuple[Maker, ...] = (
    # ti.com/lit/gpn/<part> redirects to lit/ds/symlink/<part>.pdf (checked 2026-11-05) and answers 404
    # for numbers it does not know.
    Maker("ti", "Texas Instruments", ("texasinstruments", "ti", "texasinstrumentsincorporated"),
          ("ti.com",), lambda p: f"https://www.ti.com/lit/gpn/{p.lower()}", core_first=True, voltage_suffix=True),
    Maker("st", "STMicroelectronics", ("stmicroelectronics", "st", "stmicro"),
          ("st.com",), lambda p: f"https://www.st.com/resource/en/datasheet/{p.lower()}.pdf"),
    Maker("onsemi", "onsemi", ("onsemi", "onsemiconductor", "on", "semiconductorcomponentsindustries"),
          ("onsemi.com",), lambda p: f"https://www.onsemi.com/pdf/datasheet/{p.lower()}-d.pdf"),
    Maker("diodes", "Diodes Incorporated", ("diodesincorporated", "diodes", "diodesinc"),
          ("diodes.com",), lambda p: f"https://www.diodes.com/assets/Datasheets/{p.upper()}.pdf"),
    Maker("nexperia", "Nexperia", ("nexperia", "nexperiausa"),
          ("nexperia.com",), lambda p: f"https://assets.nexperia.com/documents/data-sheet/{p.upper()}.pdf"),
)

_SUFFIXES = re.compile(r"(incorporated|inc|corporation|corp|company|co|ltd|limited|gmbh|ag|nv|bv|llc|plc|sa)$")


def _maker_keys(text: str) -> set[str]:
    """'Texas Instruments Incorporated' -> {'texasinstrumentsincorporated', 'texasinstruments'}."""
    s = re.sub(r"[^a-z0-9]", "", text.lower())
    keys = {s}
    for _ in range(2):
        stripped = _SUFFIXES.sub("", s)
        if stripped == s or not stripped:
            break
        s = stripped
        keys.add(s)
    return keys


def maker_of(manufacturer: str | None, symbol_url: str | None = None) -> Maker | None:
    """The maker named by the component's manufacturer field, else by the host of its symbol link."""
    if manufacturer:
        keys = _maker_keys(manufacturer)
        for m in MAKERS:
            if keys & (set(m.aliases) | _maker_keys(m.name)):
                return m
    if symbol_url:
        host = (urlparse(symbol_url).hostname or "").lower()
        for m in MAKERS:
            if any(host == h or host.endswith("." + h) for h in m.hosts):
                return m
    return None


def part_ladder(name: str, max_tries: int = MAX_TRIES, *, core_first: bool = False) -> list[str]:
    """Part numbers to try, from the full number down to the family core.

    'DRV8871DDAR' -> ['DRV8871DDAR', 'DRV8871DDA', 'DRV8871DD', 'DRV8871'] (the core is always kept);
    KiCad's wildcard suffix is dropped first ('STM32F103C8Tx' -> 'STM32F103C8T' ...)."""
    base = re.sub(r"x+$", "", name.strip().split("/")[0])
    base = re.split(r"[-_ ,(]", base, maxsplit=1)[0]
    base = re.sub(r"[^A-Za-z0-9]", "", base).upper()
    if len(base) < 3:
        return []
    core = part_core(base) or base
    steps = [base[:end] for end in range(len(base), max(len(core), 4) - 1, -1)] or [base]
    if len(steps) > max_tries:
        steps = steps[:max_tries - 1] + [core]
    if core not in steps:
        steps.append(core)
    if core_first:
        steps = [core] + [s for s in steps if s != core]
    return list(dict.fromkeys(steps))[:max_tries]


def without_voltage_code(core: str) -> str | None:
    """'TPS7A0233' -> 'TPS7A02', 'TLV70033' -> 'TLV700'; None when too little would be left ('DRV8871')."""
    m = re.fullmatch(r"([A-Z]+[A-Z0-9]*?)(\d{2})", core)
    if not m or sum(ch.isdigit() for ch in m.group(1)) < 3:
        return None
    return m.group(1)


def url_part_name(url: str) -> str:
    """The part name a rule-built URL asks for: '.../lit/gpn/tps7a02' -> 'TPS7A02', '.../mc7805-d.pdf' -> 'MC7805'."""
    last = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return normalize_part(re.sub(r"(?i)(-d)?\.pdf$", "", last))


def maker_candidate(component: Component, symbol_url: str | None = None) -> DatasheetCandidate | None:
    """A candidate built from the manufacturer's address pattern, or None if the maker has no known pattern."""
    maker = maker_of(component.manufacturer, symbol_url)
    if maker is None:
        return None
    names = [n for n in (component.manufacturer_part_number, symbol_part_name(component.symbol_id)) if n]
    urls: list[str] = []
    for name in names:
        steps = part_ladder(name, core_first=maker.core_first)
        if maker.voltage_suffix and steps:
            trimmed = without_voltage_code(part_core(steps[0]) or steps[0])
            if trimmed:  # right after the core: TI files fixed-voltage variants under the family name
                steps.insert(1 if maker.core_first else len(steps), trimmed)
        for step in steps:
            url = maker.url(step)
            if url not in urls and url != symbol_url:
                urls.append(url)
    if not urls:
        return None
    shown = component.manufacturer_part_number or symbol_part_name(component.symbol_id)
    return make_candidate("maker_site", f"{maker.name} 사이트 (주소 규칙)", urls[:MAX_TRIES + 3],
                          manufacturer=maker.name, part_number=shown)


def symbol_candidate(component: Component, symbol_url: str | None) -> DatasheetCandidate | None:
    if not symbol_url:
        return None
    return make_candidate("kicad_symbol", "KiCad 심볼 링크", [symbol_url],
                          part_number=symbol_part_name(component.symbol_id), description="제품군 데이터시트일 수 있음")


def search_term(component: Component) -> str | None:
    """What to ask a distributor for: the MPN, else the specific symbol's part name."""
    for name in (component.manufacturer_part_number, symbol_part_name(component.symbol_id)):
        if name and len(normalize_part(name)) >= 3:
            return name.strip()
    return None


def part_key(component: Component) -> str | None:
    """Key the distributor results are stored under (one entry per part, shared by every board)."""
    term = search_term(component)
    return normalize_part(term) if term else None


def is_public_url(url: str) -> bool:
    """A link from outside the app (a distributor's answer) must be a plain web address on a public name:
    http(s), a dotted host name, no IP literal, no localhost, no credentials in the URL."""
    try:
        u = urlparse(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https") or not host or "." not in host or u.username or u.password:
        return False
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local") or host.endswith(".internal"):
        return False
    return not re.fullmatch(r"[\d.]+|\[?[0-9a-f:]+\]?", host)
