"""Fixes for check findings that need no AI: an input pin left floating (``SINGLE_NODE_NET`` in the rules, KiCad's
``pin_not_driven``) gets a pull-up / pull-down resistor or a direct connection.

The options are suggestions computed from the pin's name and the board's rails (BOOT0 is pulled down, an active-low
SLEEP / RESET / ENABLE is pulled up to the logic supply...). Choosing one only builds ordinary patch operations; they
become a proposal that is checked and approved like any other change, with a design decision recording who chose it.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .datasheets import is_ground_name, net_voltages
from .models import CircuitIR, Component, Decision, Pin
from .patch import AddComponent, Connect, Disconnect, PinRef, SetDecision, SetNoConnect

RESISTOR = "Device:R"
RESISTOR_FOOTPRINT = "Resistor_SMD:R_0603_1608Metric"
_PULL_DOWN = re.compile(r"^(BOOT\d*|TEST|TST|MODE\d*|PROG)$")
_ACTIVE_LOW = re.compile(r"^(?:~\{(?P<a>[^}]+)\}|N(?P<b>[A-Z_]+)|(?P<c>[A-Z_]+?)_?(?:N|B))$")
_ENABLE_LIKE = re.compile(r"(SLEEP|RESET|RST|EN|ENABLE|STBY|STANDBY|SHDN|SHUTDOWN|PD|CS|OE|WP|HOLD)")
_ENABLE_HIGH = re.compile(r"^(EN|ENABLE|ON|RUN|CE)$")


@dataclass(frozen=True)
class FixOption:
    kind: str
    """``resistor`` (through a resistor), ``direct`` (wire to the net) or ``no_connect`` (left open on purpose)."""
    net: str | None
    value: str | None
    label: str
    recommended: bool = False


@dataclass
class QuickFix:
    id: str
    component_id: str
    reference: str
    pin: str
    pin_name: str | None
    electrical_type: str
    net: str | None
    problem: str
    options: list[FixOption] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _clean(name: str | None) -> str:
    return (name or "").upper().replace(" ", "")


def _logic_supply(circuit: CircuitIR, comp: Component, pin_types: dict, volts: dict) -> str | None:
    """The part's own supply rail when it is logic level, else the lowest positive rail up to 5.5 V on the board."""
    net_of = {(n.component_id, n.pin): net.name for net in circuit.nets for n in net.nodes}
    own = [net_of.get((comp.id, p.number)) for p in comp.pins if pin_types.get((comp.id, p.number)) == "power_in"]
    own = [n for n in own if n and (v := volts.get(n)) is not None and 1.5 <= v <= 5.5]
    if own:
        return min(own, key=lambda n: volts[n])
    rails = [n for n, v in volts.items() if v is not None and 1.5 <= v <= 5.5]
    return min(rails, key=lambda n: (volts[n], n)) if rails else None


def floating_pins(circuit: CircuitIR, pin_types: dict[tuple[str, str], str]) -> list[QuickFix]:
    """Input (and power input) pins that nothing drives: on no net, or alone on their net."""
    net_of = {(n.component_id, n.pin): net.name for net in circuit.nets for n in net.nodes}
    size = {net.name: len(net.nodes) for net in circuit.nets}
    volts = net_voltages(circuit)
    ground = next((n.name for n in circuit.nets if is_ground_name(n.name)), "GND")
    out: list[QuickFix] = []
    for comp in circuit.components:
        for p in comp.pins:
            etype = pin_types.get((comp.id, p.number), "")
            if etype not in ("input", "power_in") or p.no_connect:
                continue
            net = net_of.get((comp.id, p.number))
            if net is not None and size.get(net, 0) > 1:
                continue
            name = _clean(p.name)
            supply = _logic_supply(circuit, comp, pin_types, volts)
            label_pin = f"{comp.reference}.{p.number}" + (f" ({p.name})" if p.name and p.name != "~" else "")
            problem = (f"{label_pin} {'전원 입력' if etype == 'power_in' else '입력'} 핀에 아무것도 연결되지 않았습니다"
                       + (f"(Net '{net}'에 핀이 하나뿐)." if net else "."))
            fix = QuickFix(id=f"float:{comp.id}:{p.number}", component_id=comp.id, reference=comp.reference,
                           pin=p.number, pin_name=p.name, electrical_type=etype, net=net, problem=problem)
            opts: list[FixOption] = []
            if etype == "power_in":
                rails = sorted((n for n, v in volts.items() if v is not None and v > 0), key=lambda n: (volts[n], n))
                if is_ground_name(name) or name in ("VSS", "GND"):
                    opts.append(FixOption("direct", ground, None, f"{ground}에 연결", True))
                else:
                    for i, r in enumerate(rails):
                        opts.append(FixOption("direct", r, None, f"{r}에 연결", i == 0 and r == supply))
            else:
                m = _ACTIVE_LOW.match(name)
                base = next((g for g in (m.groupdict().values() if m else ()) if g), "")
                low_enable = bool(m) and bool(_ENABLE_LIKE.search(base))
                if _PULL_DOWN.match(name):
                    opts.append(FixOption("resistor", ground, "10k", f"10kΩ로 {ground}에 풀다운 (기본 동작)", True))
                    opts.append(FixOption("direct", ground, None, f"{ground}에 직접 연결"))
                elif (low_enable or _ENABLE_HIGH.match(name)) and supply:
                    opts.append(FixOption("resistor", supply, "10k", f"10kΩ로 {supply}에 풀업 (항상 동작)", True))
                    opts.append(FixOption("resistor", ground, "10k", f"10kΩ로 {ground}에 풀다운 (항상 멈춤)"))
                else:
                    opts.append(FixOption("resistor", ground, "10k", f"10kΩ로 {ground}에 풀다운", True))
                    if supply:
                        opts.append(FixOption("resistor", supply, "10k", f"10kΩ로 {supply}에 풀업"))
                    opts.append(FixOption("direct", ground, None, f"{ground}에 직접 연결"))
            fix.options = opts
            out.append(fix)
    return out


def _next_reference(circuit: CircuitIR, prefix: str, taken: set[str]) -> str:
    used = {c.reference for c in circuit.components} | taken
    n = 1
    while f"{prefix}{n}" in used:
        n += 1
    return f"{prefix}{n}"


def fix_ops(circuit: CircuitIR, fix: QuickFix, kind: str, net: str | None, value: str | None,
            taken_refs: set[str] | None = None) -> tuple[list, str]:
    """Patch operations for one chosen option, and a short Korean description of what they do."""
    taken_refs = taken_refs if taken_refs is not None else set()
    pin = PinRef(component_id=fix.component_id, pin=fix.pin)
    label = f"{fix.reference}.{fix.pin}" + (f" {fix.pin_name}" if fix.pin_name and fix.pin_name != "~" else "")
    ops: list = []
    if kind == "no_connect":
        if fix.net:
            ops.append(Disconnect(net=fix.net, nodes=[pin]))
        ops.append(SetNoConnect(component_id=fix.component_id, pin=fix.pin, no_connect=True))
        text = f"{label}: 미연결 표시"
    elif kind == "direct":
        if not net:
            raise ValueError("a direct connection needs a net")
        if fix.net:
            ops.append(Disconnect(net=fix.net, nodes=[pin]))
        ops.append(Connect(net=net, nodes=[pin]))
        text = f"{label}: {net}에 직접 연결"
    elif kind == "resistor":
        if not net:
            raise ValueError("a pull resistor needs a net")
        ref = _next_reference(circuit, "R", taken_refs)
        taken_refs.add(ref)
        rid = f"r-fix-{ref.lower()}"
        value = value or "10k"
        role = "풀다운" if is_ground_name(net) else "풀업"
        ops.append(AddComponent(component=Component(
            id=rid, reference=ref, value=value, symbol_id=RESISTOR, footprint_id=RESISTOR_FOOTPRINT,
            description=f"{label} {role}", pins=(Pin(number="1"), Pin(number="2")))))
        local = fix.net or re.sub(r"[^A-Za-z0-9_+\-]", "_", f"{fix.reference}_{fix.pin_name or fix.pin}").strip("_")
        ops.append(Connect(net=local, nodes=[pin, PinRef(component_id=rid, pin="1")]))
        ops.append(Connect(net=net, nodes=[PinRef(component_id=rid, pin="2")]))
        text = f"{label}: {ref} {value}Ω로 {net}에 {role}"
    else:
        raise ValueError(f"unknown fix kind {kind!r}")
    decision = Decision(
        id=f"dec-fix-{fix.component_id}-{fix.pin}"[:64].lower(), topic=f"{label} 처리", choice=text.split(": ", 1)[1],
        by="engineer", reason=f"검사에서 {fix.problem} 사람이 고른 조치입니다. 동작 조건은 데이터시트로 확인하세요.",
        components=[fix.component_id])
    ops.append(SetDecision(decision=decision, expect_choice=next(
        (d.choice for d in circuit.decisions if d.id == decision.id), None)))
    return ops, text
