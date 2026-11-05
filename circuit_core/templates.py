"""Reviewed circuit templates (plan Phase 3, ADR-007).

A template is a small reference circuit taken from a manufacturer's datasheet application circuit: the
parts, their connections, the datasheet sections it rests on, and a checklist an engineer works through
before trusting it on a board. Templates are code in this repository (changed only through review) and are
versioned; a part placed from a template carries ``origin = "template:<id>@<version>"``.

Policy (ADR-007):
- A template is a starting point, not a verified design. Placing one never marks anything ``verified``;
  the board's engineering review (``backend/reviews.py``) is the only place that does.
- Every template names the datasheet (document, link from the KiCad symbol) and the section each choice
  comes from. Page numbers are recorded by the engineer who checks the datasheet; the app never guesses them.
- Values the datasheet leaves to the designer (inductor, load capacitors, termination) are marked in the
  checklist as decisions to confirm.

Templates reach a board only as a proposal, through the normal approval path.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from .models import CircuitIR, Component, Decision, Net, NetNode, Note, Pin

PinsOf = Callable[[str], "list[tuple[str, str]] | None"]
"""symbol id -> [(pin number, pin name)] from the installed KiCad library, or None if it is not there."""

_NET = re.compile(r"^[A-Za-z0-9_+\-]{1,64}$")


class TemplateError(ValueError):
    pass


@dataclass(frozen=True)
class Basis:
    """Where a template's choice comes from."""

    part: str
    document: str
    section: str
    supports: str
    url: str | None = None
    page: str | None = None
    """Left empty: the engineer who checks the datasheet records the page in the board's review."""


@dataclass(frozen=True)
class Check:
    id: str
    text: str
    basis: str
    """Datasheet section (or design practice) the item is checked against."""


@dataclass(frozen=True)
class Param:
    name: str
    label: str
    default: str
    kind: Literal["net", "value", "flag"] = "net"


@dataclass(frozen=True)
class Part:
    key: str
    prefix: str
    value: str
    symbol_id: str
    role: str
    footprint_id: str | None = None
    manufacturer: str | None = None
    mpn: str | None = None
    no_connect: tuple[str, ...] = ()


@dataclass
class Fragment:
    parts: list[Part]
    nets: dict[str, list[tuple[str, str]]]
    """net name -> [(part key, pin number)]"""


@dataclass(frozen=True)
class Template:
    id: str
    version: int
    title: str
    category: str
    summary: str
    params: tuple[Param, ...]
    basis: tuple[Basis, ...]
    checklist: tuple[Check, ...]
    build: Callable[[dict[str, str]], Fragment] = field(repr=False, compare=False)
    caption: str = ""
    """English caption drawn above the block on the schematic; ``{param}`` takes a parameter value."""

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    @property
    def origin(self) -> str:
        return f"template:{self.ref}"

    def ports(self) -> list[str]:
        return [p.name for p in self.params if p.kind == "net"]

    def to_dict(self) -> dict:
        defaults = {p.name: p.default for p in self.params}
        frag = self.build(defaults)
        return {
            "id": self.id, "version": self.version, "ref": self.ref, "title": self.title, "category": self.category,
            "summary": self.summary,
            "params": [{"name": p.name, "label": p.label, "default": p.default, "kind": p.kind} for p in self.params],
            "basis": [b.__dict__ for b in self.basis],
            "checklist": [c.__dict__ for c in self.checklist],
            "parts": [{"key": p.key, "prefix": p.prefix, "value": p.value, "symbol_id": p.symbol_id, "role": p.role,
                       "footprint_id": p.footprint_id, "mpn": p.mpn} for p in frag.parts],
        }


# --- templates ------------------------------------------------------------------------------------------

_C0603 = "Capacitor_SMD:C_0603_1608Metric"
_C0805 = "Capacitor_SMD:C_0805_2012Metric"
_R0603 = "Resistor_SMD:R_0603_1608Metric"


def _ldo(p: dict[str, str]) -> Fragment:
    vin, vout, gnd = p["vin"], p["vout"], p["gnd"]
    return Fragment(
        parts=[
            Part("u", "U", "AMS1117-3.3", "Regulator_Linear:AMS1117-3.3", "3.3V 선형 레귤레이터",
                 "Package_TO_SOT_SMD:SOT-223-3_TabPin2", "Advanced Monolithic Systems", "AMS1117-3.3"),
            Part("cin", "C", "10uF", "Device:C", "입력 커패시터", _C0805),
            Part("cout", "C", "22uF", "Device:C_Polarized", "출력 커패시터 (탄탈)",
                 "Capacitor_Tantalum_SMD:CP_EIA-3528-21_Kemet-B"),
        ],
        nets={vin: [("u", "3"), ("cin", "1")], vout: [("u", "2"), ("cout", "1")],
              gnd: [("u", "1"), ("cin", "2"), ("cout", "2")]})


def _buck(p: dict[str, str]) -> Fragment:
    vin, vout, gnd = p["vin"], p["vout"], p["gnd"]
    return Fragment(
        parts=[
            Part("u", "U", "LM2596S-5", "Regulator_Switching:LM2596S-5", "5V 고정 출력 벅 컨버터",
                 "Package_TO_SOT_SMD:TO-263-5_TabPin3", "Texas Instruments", "LM2596S-5.0"),
            Part("cin", "C", "680uF", "Device:C_Polarized", "입력 커패시터 (전해)", "Capacitor_SMD:CP_Elec_10x10.5"),
            Part("d", "D", "1N5822", "Diode:1N5822", "환류 쇼트키 다이오드", "Diode_THT:D_DO-201AD_P15.24mm_Horizontal",
                 "Vishay", "1N5822"),
            Part("l", "L", "33uH", "Device:L", "출력 인덕터", "Inductor_SMD:L_12x12mm_H8mm"),
            Part("cout", "C", "220uF", "Device:C_Polarized", "출력 커패시터 (전해)", "Capacitor_SMD:CP_Elec_8x10.5"),
        ],
        nets={vin: [("u", "1"), ("cin", "1")],
              gnd: [("u", "3"), ("u", "5"), ("cin", "2"), ("d", "2"), ("cout", "2")],
              "SW": [("u", "2"), ("d", "1"), ("l", "1")],
              vout: [("l", "2"), ("cout", "1"), ("u", "4")]})


def _can(p: dict[str, str]) -> Fragment:
    parts = [
        Part("u", "U", "SN65HVD230", "Interface_CAN_LIN:SN65HVD230", "3.3V CAN 트랜시버",
             "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "Texas Instruments", "SN65HVD230DR", no_connect=("5",)),
        Part("c", "C", "100nF", "Device:C", "VCC 디커플링", _C0603),
    ]
    nets = {p["vcc"]: [("u", "3"), ("c", "1")], p["gnd"]: [("u", "2"), ("u", "8"), ("c", "2")],
            p["txd"]: [("u", "1")], p["rxd"]: [("u", "4")], p["canh"]: [("u", "7")], p["canl"]: [("u", "6")]}
    if p["termination"] == "yes":
        parts.append(Part("r", "R", "120", "Device:R", "버스 종단 저항 (버스 끝 노드만)", _R0603))
        nets[p["canh"]].append(("r", "1"))
        nets[p["canl"]].append(("r", "2"))
    return Fragment(parts, nets)


_ATMEGA_USED = {"3", "4", "5", "6", "7", "8", "18", "20", "21", "29", "30", "31"}


def _mcu(p: dict[str, str]) -> Fragment:
    vcc, gnd = p["vcc"], p["gnd"]
    unused = tuple(str(n) for n in range(1, 33) if str(n) not in _ATMEGA_USED)
    return Fragment(
        parts=[
            Part("u", "U", "ATmega328P-A", "MCU_Microchip_ATmega:ATmega328P-A", "8비트 MCU",
                 "Package_QFP:TQFP-32_7x7mm_P0.8mm", "Microchip", "ATmega328P-AU", no_connect=unused),
            Part("cvcc", "C", "100nF", "Device:C", "VCC 디커플링", _C0603),
            Part("cavcc", "C", "100nF", "Device:C", "AVCC 디커플링", _C0603),
            Part("caref", "C", "100nF", "Device:C", "AREF 바이패스", _C0603),
            Part("y", "Y", p["crystal"], "Device:Crystal", "MCU 클럭 크리스탈", "Crystal:Crystal_HC49-4H_Vertical"),
            Part("cx1", "C", "22pF", "Device:C", "크리스탈 부하 커패시터", _C0603),
            Part("cx2", "C", "22pF", "Device:C", "크리스탈 부하 커패시터", _C0603),
            Part("rrst", "R", "10k", "Device:R", "RESET 풀업", _R0603),
        ],
        nets={vcc: [("u", "4"), ("u", "6"), ("u", "18"), ("cvcc", "1"), ("cavcc", "1"), ("rrst", "1")],
              gnd: [("u", "3"), ("u", "5"), ("u", "21"), ("cvcc", "2"), ("cavcc", "2"), ("caref", "2"),
                    ("cx1", "2"), ("cx2", "2")],
              "XTAL1": [("u", "7"), ("y", "1"), ("cx1", "1")],
              "XTAL2": [("u", "8"), ("y", "2"), ("cx2", "1")],
              "AREF": [("u", "20"), ("caref", "1")],
              p["reset"]: [("u", "29"), ("rrst", "2")],
              p["rxd"]: [("u", "30")],
              p["txd"]: [("u", "31")]})


_AMS = "AMS1117 데이터시트 (Advanced Monolithic Systems)"
_LM2596 = "LM2596 데이터시트 (Texas Instruments, SNVS124)"
_HVD230 = "SN65HVD230 데이터시트 (Texas Instruments, SLOS346)"
_M328 = "ATmega328P 데이터시트 (Microchip, DS40001984)"

TEMPLATES: dict[str, Template] = {t.id: t for t in (
    Template(
        id="ldo-ams1117-3v3", version=1, title="3.3V LDO (AMS1117-3.3)", category="전원",
        summary="5V 등에서 3.3V를 만드는 선형 레귤레이터. 입력 10µF, 출력 탄탈 22µF.",
        params=(Param("vin", "입력 전원 Net", "+5V"), Param("vout", "출력 전원 Net", "+3V3"),
                Param("gnd", "GND Net", "GND")),
        basis=(
            Basis("AMS1117", _AMS, "Typical Applications", "입력·출력 커패시터 연결",
                  "http://www.advanced-monolithic.com/pdf/ds1117.pdf"),
            Basis("AMS1117", _AMS, "Application Hints — Stability", "출력 커패시터 용량·종류(안정 조건)",
                  "http://www.advanced-monolithic.com/pdf/ds1117.pdf"),
            Basis("AMS1117", _AMS, "Electrical Characteristics — Dropout Voltage", "최소 입력 전압",
                  "http://www.advanced-monolithic.com/pdf/ds1117.pdf"),
        ),
        checklist=(
            Check("dropout", "입력 전압이 출력 전압 + 드롭아웃 전압 이상인가 (부하 전류 최대일 때)",
                  "Electrical Characteristics — Dropout Voltage"),
            Check("cout", "출력 커패시터의 용량·ESR이 데이터시트 안정 조건에 맞는가 (템플릿: 탄탈 22µF)",
                  "Application Hints — Stability"),
            Check("thermal", "손실 (Vin − Vout) × Iout이 SOT-223 방열 한계 안인가", "Thermal Considerations"),
            Check("place", "입력·출력 커패시터를 레귤레이터 핀 가까이 배치하는가", "Typical Applications"),
        ),
        build=_ldo, caption="3.3 V LDO regulator (AMS1117-3.3)"),
    Template(
        id="buck-lm2596-5v", version=1, title="5V 벅 컨버터 (LM2596S-5)", category="전원",
        summary="7~40V 입력에서 5V를 만드는 스위칭 레귤레이터. 입력 680µF, 33µH, 1N5822, 출력 220µF.",
        params=(Param("vin", "입력 전원 Net", "+12V"), Param("vout", "출력 전원 Net", "+5V"),
                Param("gnd", "GND Net", "GND")),
        basis=(
            Basis("LM2596", _LM2596, "Typical Application (Fixed Output Voltage Versions)",
                  "부품 연결과 기본값(입력·출력 커패시터, 인덕터, 다이오드)", "http://www.ti.com/lit/ds/symlink/lm2596.pdf"),
            Basis("LM2596", _LM2596, "Inductor Selection", "입력 전압·부하 전류에 따른 인덕터 값",
                  "http://www.ti.com/lit/ds/symlink/lm2596.pdf"),
            Basis("LM2596", _LM2596, "Catch Diode / Output Capacitor Selection", "다이오드·출력 커패시터 조건",
                  "http://www.ti.com/lit/ds/symlink/lm2596.pdf"),
            Basis("1N5822", "1N5820–1N5822 데이터시트 (Vishay)", "Maximum Ratings", "역전압·평균 전류",
                  "http://www.vishay.com/docs/88526/1n5820.pdf"),
        ),
        checklist=(
            Check("vin", "최대 입력 전압이 LM2596 정격(40V)과 입력 커패시터 정격 전압 안인가", "Absolute Maximum Ratings"),
            Check("inductor", "인덕터 값(템플릿 33µH)을 입력 전압·부하 전류로 인덕터 선택 그래프에서 확인했는가",
                  "Inductor Selection"),
            Check("isat", "인덕터 포화 전류가 최대 부하 전류 + 리플의 절반보다 큰가", "Inductor Selection"),
            Check("diode", "다이오드가 쇼트키이고 역전압 ≥ 입력 전압, 전류 ≥ 부하 전류인가", "Catch Diode Selection"),
            Check("cout", "출력 커패시터의 ESR·리플 전류 정격이 맞는가", "Output Capacitor Selection"),
            Check("layout", "스위칭 루프(입력 커패시터–VIN–GND–다이오드)를 짧고 굵게 배선하는가", "Layout Guidelines"),
        ),
        build=_buck, caption="5 V buck regulator (LM2596S-5)"),
    Template(
        id="can-sn65hvd230", version=1, title="CAN 트랜시버 (SN65HVD230)", category="통신",
        summary="3.3V CAN 트랜시버. VCC 디커플링 100nF, Rs는 GND(고속 모드), 버스 끝단이면 120Ω 종단.",
        params=(Param("vcc", "3.3V 전원 Net", "+3V3"), Param("gnd", "GND Net", "GND"),
                Param("txd", "MCU → 트랜시버 송신 Net", "TXD"), Param("rxd", "트랜시버 → MCU 수신 Net", "RXD"),
                Param("canh", "CANH Net", "CANH"), Param("canl", "CANL Net", "CANL"),
                Param("termination", "120Ω 종단 저항 (yes/no)", "yes", "flag")),
        basis=(
            Basis("SN65HVD230", _HVD230, "Typical Application", "MCU·버스 연결, 디커플링",
                  "http://www.ti.com/lit/ds/symlink/sn65hvd230.pdf"),
            Basis("SN65HVD230", _HVD230, "Function Modes — Rs pin", "Rs를 GND에 두면 고속 모드",
                  "http://www.ti.com/lit/ds/symlink/sn65hvd230.pdf"),
            Basis("SN65HVD230", _HVD230, "Recommended Operating Conditions", "전원 전압 범위",
                  "http://www.ti.com/lit/ds/symlink/sn65hvd230.pdf"),
        ),
        checklist=(
            Check("vcc", "VCC가 데이터시트 권장 동작 전압(3.3V 계열) 안인가", "Recommended Operating Conditions"),
            Check("rs", "Rs 모드가 맞는가 (GND = 고속, 저항 = 슬로프 제어, 높음 = 대기)", "Function Modes — Rs pin"),
            Check("term", "120Ω 종단은 버스 양 끝 노드에만 있는가 (이 노드가 끝단이 아니면 빼기)", "Typical Application"),
            Check("decoupling", "100nF 디커플링을 VCC 핀 가까이 배치하는가", "Typical Application"),
            Check("protection", "버스 쪽 ESD/과전압 보호(TVS 등)가 필요한지 검토했는가", "설계 판단"),
        ),
        build=_can, caption="CAN transceiver (SN65HVD230), 3.3 V"),
    Template(
        id="mcu-atmega328p-min", version=1, title="MCU 최소 회로 (ATmega328P)", category="MCU",
        summary="ATmega328P-AU 최소 회로: VCC·AVCC·AREF 100nF, 크리스탈과 22pF 두 개, RESET 10k 풀업, UART 핀.",
        params=(Param("vcc", "전원 Net", "+5V"), Param("gnd", "GND Net", "GND"),
                Param("crystal", "크리스탈 주파수", "16MHz", "value"),
                Param("reset", "RESET Net", "RESET"), Param("rxd", "UART 수신(PD0) Net", "RXD"),
                Param("txd", "UART 송신(PD1) Net", "TXD")),
        basis=(
            Basis("ATmega328P", _M328, "Electrical Characteristics — Speed Grades", "전원 전압별 최대 클럭",
                  "http://ww1.microchip.com/downloads/en/DeviceDoc/ATmega328_P%20AVR%20MCU%20with%20picoPower%20Technology%20Data%20Sheet%2040001984A.pdf"),
            Basis("ATmega328P", _M328, "System Clock and Clock Options — Low Power Crystal Oscillator",
                  "크리스탈 부하 커패시터 범위",
                  "http://ww1.microchip.com/downloads/en/DeviceDoc/ATmega328_P%20AVR%20MCU%20with%20picoPower%20Technology%20Data%20Sheet%2040001984A.pdf"),
            Basis("ATmega328P", _M328, "Pin Descriptions — AVCC, AREF", "AVCC는 VCC에 연결, AREF 바이패스",
                  "http://ww1.microchip.com/downloads/en/DeviceDoc/ATmega328_P%20AVR%20MCU%20with%20picoPower%20Technology%20Data%20Sheet%2040001984A.pdf"),
            Basis("ATmega328P", _M328, "System Control and Reset", "RESET 핀과 풀업",
                  "http://ww1.microchip.com/downloads/en/DeviceDoc/ATmega328_P%20AVR%20MCU%20with%20picoPower%20Technology%20Data%20Sheet%2040001984A.pdf"),
        ),
        checklist=(
            Check("speed", "전원 전압에서 크리스탈 주파수가 허용되는가 (5V면 16MHz 가능, 3.3V면 8MHz 이하)",
                  "Electrical Characteristics — Speed Grades"),
            Check("load", "부하 커패시터(템플릿 22pF)가 크리스탈의 CL 사양과 데이터시트 범위에 맞는가",
                  "Low Power Crystal Oscillator"),
            Check("avcc", "AVCC를 VCC에 연결했는가 (ADC를 쓰면 LC 필터 검토)", "Pin Descriptions — AVCC"),
            Check("decoupling", "VCC/GND 쌍마다 100nF를 핀 가까이 배치하는가", "설계 판단"),
            Check("reset", "RESET 풀업(10k)과 프로그래밍/디버그 방식(ISP 등)이 맞는가", "System Control and Reset"),
            Check("uart", "UART 신호 레벨이 연결할 장치와 맞는가 (VCC 레벨)", "설계 판단"),
            Check("unused", "쓰지 않는 핀(템플릿에서 no-connect)을 실제 용도에 맞게 다시 정했는가", "설계 판단"),
        ),
        build=_mcu, caption="MCU core: ATmega328P, {crystal} crystal"),
)}


# --- placing a template ----------------------------------------------------------------------------------

@dataclass
class Instance:
    template: Template
    params: dict[str, str]
    components: list[Component]
    nets: dict[str, list[tuple[str, str]]]
    """net name -> [(component id, pin)] after renaming; port nets keep their names and join the board's."""

    @property
    def decision(self) -> Decision:
        """Why the block is what it is: the reviewed template, with its datasheet basis."""
        params = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return Decision(id=f"dec-tpl-{self.components[0].id}"[:64], topic=f"템플릿: {self.template.title}"[:120],
                        choice=f"{self.template.ref} ({params})"[:300], by="template",
                        reason=f"{self.template.summary} 부품 값과 연결은 검토된 템플릿이 정합니다. 체크리스트 "
                               f"{len(self.template.checklist)}개는 엔지니어 검토에서 확인합니다."[:2000],
                        source=template_sources(self.template)[:500] or None,
                        components=tuple(c.id for c in self.components))

    @property
    def note(self) -> Note | None:
        """The template's English caption, attached to its parts."""
        if not self.template.caption or not self.components:
            return None
        text = self.template.caption.format(**self.params)
        return Note(id=f"note-{self.components[0].id}"[:64], text=text[:120],
                    components=tuple(c.id for c in self.components))


def resolve_params(t: Template, values: dict[str, str] | None) -> dict[str, str]:
    values = values or {}
    unknown = set(values) - {p.name for p in t.params}
    if unknown:
        raise TemplateError(f"{t.title}: 모르는 파라미터 {', '.join(sorted(unknown))}")
    out = {}
    for p in t.params:
        v = str(values.get(p.name, p.default)).strip() or p.default
        if p.kind == "net" and not _NET.fullmatch(v):
            raise TemplateError(f"{p.label}: Net 이름 '{v}'에는 영문·숫자·_+- 만 쓸 수 있습니다.")
        if p.kind == "flag" and v not in ("yes", "no"):
            raise TemplateError(f"{p.label}: yes 또는 no로 적으세요.")
        out[p.name] = v
    ports = [out[p.name] for p in t.params if p.kind == "net"]
    if len(set(ports)) != len(ports):
        raise TemplateError(f"{t.title}: 서로 다른 단자에 같은 Net 이름을 줄 수 없습니다.")
    return out


def instantiate(t: Template, values: dict[str, str] | None, base: CircuitIR | None, pins_of: PinsOf, *,
                reserved: tuple[set[str], set[str], set[str]] | None = None) -> Instance:
    """The template's parts with ids and references that do not clash with ``base``; port nets keep their
    names (so they join the board's nets of the same name), internal nets are renamed if taken.

    ``reserved`` = (ids, references, net names) also taken, e.g. by an AI design being assembled; the sets
    are updated with what this template takes."""
    params = resolve_params(t, values)
    frag = t.build(params)
    taken_ids, taken_refs, base_nets = reserved if reserved is not None else (set(), set(), set())
    if base:
        taken_ids |= {c.id for c in base.components}
        taken_refs |= {c.reference for c in base.components}
        base_nets |= {n.name for n in base.nets}
    ports = {params[p.name] for p in t.params if p.kind == "net"}

    ids: dict[str, str] = {}
    components: list[Component] = []
    for part in frag.parts:
        stem = re.sub(r"[^a-z0-9]+", "-", f"{t.id.split('-')[0]}-{part.key}".lower()).strip("-")
        cid, n = stem, 2
        while cid in taken_ids:
            cid, n = f"{stem}-{n}", n + 1
        taken_ids.add(cid)
        ids[part.key] = cid
        number = 1
        while f"{part.prefix}{number}" in taken_refs:
            number += 1
        ref = f"{part.prefix}{number}"
        taken_refs.add(ref)
        lib_pins = pins_of(part.symbol_id)
        if lib_pins is None:
            raise TemplateError(f"{t.title}: KiCad 라이브러리에 {part.symbol_id} 심볼이 없습니다.")
        components.append(Component(
            id=cid, reference=ref, value=part.value, symbol_id=part.symbol_id, footprint_id=part.footprint_id,
            manufacturer=part.manufacturer, manufacturer_part_number=part.mpn, description=part.role,
            origin=t.origin,
            pins=tuple(Pin(number=num, name=name or None, no_connect=num in part.no_connect) for num, name in lib_pins)))

    nets: dict[str, list[tuple[str, str]]] = {}
    for name, nodes in frag.nets.items():
        final = name
        if name not in ports and name in base_nets:
            n = 2
            while f"{name}_{n}" in base_nets or f"{name}_{n}" in frag.nets:
                n += 1
            final = f"{name}_{n}"
        nets[final] = [(ids[key], pin) for key, pin in nodes]
        if final not in ports:
            base_nets.add(final)

    connected = {(cid, pin) for nodes in nets.values() for cid, pin in nodes}
    for comp in components:
        for pin in comp.pins:
            if (comp.id, pin.number) not in connected and not pin.no_connect:
                raise TemplateError(f"{t.ref}: {comp.reference}.{pin.number} 핀이 템플릿에서 비어 있습니다 (템플릿 오류).")
    return Instance(t, params, components, nets)


def as_circuit(inst: Instance, project_id: str, title: str | None = None) -> CircuitIR:
    """A board that holds only this template (used when the board is still empty)."""
    return CircuitIR(
        project_id=project_id, title=title or inst.template.title, components=tuple(inst.components),
        nets=tuple(Net(id=f"net-{i:03d}", name=name, nodes=tuple(NetNode(component_id=c, pin=p) for c, p in nodes))
                   for i, (name, nodes) in enumerate(inst.nets.items(), start=1)),
        notes=(inst.note,) if inst.note else (), decisions=(inst.decision,))


def template_sources(t: Template) -> str:
    """The template's datasheet basis in one line: 'document - section (supports); ...'."""
    return "; ".join(f"{b.document} — {b.section} ({b.supports})" for b in t.basis)


def catalog_text() -> str:
    """Short catalog for AI prompts."""
    lines = []
    for t in TEMPLATES.values():
        params = ", ".join(f"{p.name}(기본 {p.default})" for p in t.params)
        lines.append(f"- {t.id}: {t.title}. {t.summary} 파라미터: {params}")
    return "\n".join(lines)
