"""Technical review by calculation (plan section 10, level L3 pre-checks): no AI, only the circuit and the rules.

Each check states what it calculated and what it assumed, so a reviewer can redo it. Results are ``ok``,
``info``, ``warn`` or ``fail``. They point at things to look at; they do not verify a design (only an engineer's
review record does that, plan section 10.1).

Voltages come from net names (``+5V``, ``+3V3``, ``GND``). Nets without a voltage in their name are left out of
voltage-based checks rather than guessed.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .datasheets import is_ground_name, net_voltage
from .export import natural_key
from .models import CircuitIR, Component
from .parts import parse_value

Severity = str  # "ok" | "info" | "warn" | "fail"
ORDER = {"fail": 0, "warn": 1, "info": 2, "ok": 3}

# Power ratings of chip resistors by size (common 1/16 W .. 1 W ratings; check the part's datasheet).
_R_POWER = [("0201", 0.05), ("0402", 0.0625), ("0603", 0.1), ("0805", 0.125), ("1206", 0.25), ("1210", 0.5),
            ("2010", 0.75), ("2512", 1.0)]
# Typical LED forward voltage ranges by colour (V), used when the value names a colour.
_LED_VF = [("RED", (1.8, 2.2)), ("ORANGE", (1.9, 2.2)), ("YELLOW", (1.9, 2.2)), ("AMBER", (1.9, 2.2)),
           ("GREEN", (2.0, 3.2)), ("BLUE", (2.8, 3.4)), ("WHITE", (2.8, 3.4))]
_POWER_PINS = re.compile(r"^(VCC|VDD|AVCC|AVDD|DVDD|VDDA|VDDIO|V\+|VS|VBAT|VIN|VI|IN)$", re.I)
_IN_PINS = re.compile(r"^(IN|VI|VIN)$", re.I)
_OUT_PINS = re.compile(r"^(OUT|VO|VOUT)$", re.I)


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: Severity
    title: str
    detail: str
    calc: str | None = None
    components: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self, refs: dict[str, str]) -> dict:
        return {"rule": self.rule, "severity": self.severity, "title": self.title, "detail": self.detail,
                "calc": self.calc, "components": list(self.components),
                "references": sorted((refs.get(c, c) for c in self.components), key=natural_key)}


def _fmt(v: float, unit: str) -> str:
    for scale, prefix in ((1e6, "M"), (1e3, "k"), (1.0, ""), (1e-3, "m"), (1e-6, "µ")):
        if abs(v) >= scale or scale == 1e-6:
            return f"{v / scale:.3g} {prefix}{unit}"
    return f"{v} {unit}"


def _rated_volts(value: str) -> float | None:
    m = re.search(r"(?:^|[/\s,_])(\d+(?:\.\d+)?)\s*V\b", value, re.I)
    return float(m.group(1)) if m else None


def _r_rating(comp: Component) -> float | None:
    fp = (comp.footprint_id or "").upper()
    for size, watts in _R_POWER:
        if f"_{size}_" in fp or fp.endswith(f"_{size}") or f"R_{size}" in fp:
            return watts
    if "AXIAL" in fp:
        return 0.25
    return None


def _kind(comp: Component) -> str:
    sym = comp.symbol_id.split(":")[-1].upper()
    lib = comp.symbol_id.split(":")[0]
    if sym.startswith("LED"):
        return "led"
    if lib == "Device" and (sym == "R" or sym.startswith("R_")):
        return "r"
    if lib == "Device" and (sym == "C" or sym.startswith("C_") or sym.startswith("CP")):
        return "cp" if "POL" in sym or sym.startswith("CP") else "c"
    if lib == "Regulator_Linear" or re.match(r"^(L?M?78\d\d|LM1117|AMS1117|LD1117)", comp.value.upper()):
        return "linreg"
    return "other"


class _Board:
    def __init__(self, circuit: CircuitIR):
        self.c = circuit
        self.net_of = {(n.component_id, n.pin): net.name for net in circuit.nets for n in net.nodes}
        self.nodes = {net.name: [(n.component_id, n.pin) for n in net.nodes] for net in circuit.nets}
        self.comps = {c.id: c for c in circuit.components}

    def v(self, net: str | None) -> float | None:
        return None if net is None else net_voltage(net)

    def pins_named(self, comp: Component, pattern: re.Pattern) -> list[str]:
        return [p.number for p in comp.pins if p.name and pattern.match(p.name)]

    def pin_by_name(self, comp: Component, name: str) -> str | None:
        return next((p.number for p in comp.pins if (p.name or "").upper() == name), None)

    def two_nets(self, comp: Component) -> tuple[str | None, str | None]:
        nums = [p.number for p in comp.pins]
        return (self.net_of.get((comp.id, nums[0])), self.net_of.get((comp.id, nums[1]))) if len(nums) == 2 else (None, None)

    def supply_of(self, comp_id: str) -> float | None:
        """Highest named supply voltage on an IC's power pins (an upper bound for its output high level)."""
        comp = self.comps[comp_id]
        volts = [self.v(self.net_of.get((comp_id, n))) for n in self.pins_named(comp, _POWER_PINS)]
        volts = [x for x in volts if x is not None and x > 0]
        return max(volts) if volts else None


def _capacitors(b: _Board) -> list[Finding]:
    out = []
    for comp in b.c.components:
        kind = _kind(comp)
        if kind not in ("c", "cp"):
            continue
        n1, n2 = b.two_nets(comp)
        v1, v2 = b.v(n1), b.v(n2)
        if v1 is None or v2 is None:
            continue
        across = abs(v1 - v2)
        rated = _rated_volts(comp.value)
        name = f"{comp.reference} ({comp.value})"
        if rated is None:
            if across > 0:
                out.append(Finding("cap_voltage", "info", f"{name}: 정격 전압 미기재",
                                   f"양단 {across:g} V. 정격 {math.ceil(across * 1.5)} V 이상(1.5배 여유)을 고르세요.",
                                   f"|{n1} − {n2}| = {across:g} V", (comp.id,)))
        elif rated < across:
            out.append(Finding("cap_voltage", "fail", f"{name}: 정격 전압 부족",
                               f"정격 {rated:g} V < 양단 {across:g} V.", f"{rated:g} V < {across:g} V", (comp.id,)))
        elif rated < 1.5 * across:
            out.append(Finding("cap_voltage", "warn", f"{name}: 정격 전압 여유 부족",
                               f"정격 {rated:g} V, 양단 {across:g} V(정격의 {across / rated:.0%}). 1.5배 이상 여유를 권장합니다"
                               "(세라믹은 DC 바이어스로 용량이 줄어듦).",
                               f"{rated:g} / {across:g} = {rated / across:.2f}배", (comp.id,)))
        else:
            out.append(Finding("cap_voltage", "ok", f"{name}: 정격 전압 여유 충분",
                               f"정격 {rated:g} V, 양단 {across:g} V.", f"{rated:g} / {across:g} = {rated / across:.2f}배",
                               (comp.id,)))
        if kind == "cp":
            plus = b.v(b.net_of.get((comp.id, comp.pins[0].number)))
            minus = b.v(b.net_of.get((comp.id, comp.pins[1].number)))
            if plus is not None and minus is not None:
                ok = plus >= minus
                out.append(Finding("polarity", "ok" if ok else "fail",
                                   f"{name}: 극성 {'맞음' if ok else '반대'}",
                                   f"+ 단자(1번) {plus:g} V, − 단자 {minus:g} V.", None, (comp.id,)))
    return out


def _vf(comp: Component) -> tuple[tuple[float, float], bool]:
    text = f"{comp.value} {comp.description or ''}".upper()
    for colour, vf in _LED_VF:
        if colour in text:
            return vf, True
    return (1.8, 3.4), False


def _leds(b: _Board) -> list[Finding]:
    out = []
    for comp in b.c.components:
        if _kind(comp) != "led":
            continue
        a, k = b.pin_by_name(comp, "A"), b.pin_by_name(comp, "K")
        if a is None or k is None:
            continue
        na, nk = b.net_of.get((comp.id, a)), b.net_of.get((comp.id, k))
        va, vk = b.v(na), b.v(nk)
        name = f"{comp.reference} ({comp.value})"
        if va is not None and vk is not None:
            if va < vk:
                out.append(Finding("led_direction", "fail", f"{name}: 방향 반대", f"애노드 {va:g} V < 캐소드 {vk:g} V.",
                                   None, (comp.id,)))
            elif va > vk:
                out.append(Finding("led_series_r", "fail", f"{name}: 직렬 저항 없음",
                                   f"LED가 {na}({va:g} V)와 {nk}({vk:g} V) 사이에 바로 연결되어 있습니다. 전류 제한 저항이 필요합니다.",
                                   None, (comp.id,)))
            continue
        # A resistor in series on one side, the other side on a known voltage.
        for side_net, other_v, at_anode in ((na, vk, True), (nk, va, False)):
            if side_net is None or other_v is None:
                continue
            series = [b.comps[c] for c, _ in b.nodes.get(side_net, []) if c != comp.id]
            if len(series) != 1 or _kind(series[0]) != "r":
                continue
            r = series[0]
            ohms = parse_value(r.value)
            far = next((n for n in b.two_nets(r) if n != side_net), None)
            if not ohms or far is None:
                continue
            v_far, bound = b.v(far), ""
            if v_far is None:  # driven by an IC pin: its supply is the most it can output
                drivers = [c for c, _ in b.nodes.get(far, []) if c not in (r.id,) and len(b.comps[c].pins) >= 3]
                supplies = [s for s in (b.supply_of(c) for c in drivers) if s is not None]
                if not supplies:
                    continue
                v_far, bound = max(supplies), f" (구동 IC 전원 {max(supplies):g} V를 출력 최대값으로 가정)"
            v_hi, v_lo = (v_far, other_v) if at_anode else (other_v, v_far)
            (vf_lo, vf_hi), known = _vf(comp)
            i_max = (v_hi - v_lo - vf_lo) / ohms
            i_min = (v_hi - v_lo - vf_hi) / ohms
            if i_max <= 0:
                out.append(Finding("led_current", "warn", f"{name}: 켜지지 않을 수 있음",
                                   f"구동 전압 {v_hi - v_lo:g} V가 Vf {vf_lo:g} V 이하입니다.", None, (comp.id, r.id)))
                continue
            i_min = max(i_min, 0.0)
            vf_note = f"Vf {vf_lo:g}~{vf_hi:g} V({'색 기준' if known else '색 미기재, 넓은 범위'})"
            calc = (f"I = ({v_hi:g} V − {v_lo:g} V − Vf) / {_fmt(ohms, 'Ω')} = {_fmt(i_min, 'A')} ~ {_fmt(i_max, 'A')}, "
                    f"{vf_note}{bound}")
            sev = "warn" if i_max > 0.02 else "ok"
            out.append(Finding("led_current", sev, f"{name}: 전류 {_fmt(i_min, 'A')} ~ {_fmt(i_max, 'A')}",
                               "표시용 LED는 보통 20 mA 이하로 씁니다. 실제 값은 LED·구동 IC 데이터시트로 확인하세요."
                               if sev == "warn" else "표시용 LED 범위(20 mA 이하)입니다. Vf는 LED 데이터시트로 확인하세요.",
                               calc, (comp.id, r.id)))
            out += _r_power(r, i_max * i_max * ohms, f"P = I² × R = ({_fmt(i_max, 'A')})² × {_fmt(ohms, 'Ω')}")
            break
    return out


def _r_power(r: Component, watts: float, calc: str) -> list[Finding]:
    rating = _r_rating(r)
    name = f"{r.reference} ({r.value})"
    if rating is None:
        return [Finding("r_power", "info", f"{name}: 소비 전력 {_fmt(watts, 'W')}", "풋프린트로 정격을 알 수 없어 비교하지 않았습니다.",
                        calc, (r.id,))]
    sev = "fail" if watts > rating else "warn" if watts > rating / 2 else "ok"
    return [Finding("r_power", sev, f"{name}: 소비 전력 {_fmt(watts, 'W')} / 정격 {rating:g} W",
                    "정격의 50% 이하를 권장합니다." if sev != "ok" else "정격의 50% 이하입니다.",
                    f"{calc}; 정격은 크기 기준 일반값({rating:g} W), 부품 데이터시트로 확인", (r.id,))]


def _resistors_across_rails(b: _Board, seen: set[str]) -> list[Finding]:
    out = []
    for comp in b.c.components:
        if _kind(comp) != "r" or comp.id in seen:
            continue
        n1, n2 = b.two_nets(comp)
        v1, v2, ohms = b.v(n1), b.v(n2), parse_value(comp.value)
        if v1 is None or v2 is None or not ohms or v1 == v2:
            continue
        dv = abs(v1 - v2)
        out += _r_power(comp, dv * dv / ohms, f"P = ΔV² / R = ({dv:g} V)² / {_fmt(ohms, 'Ω')}")
    return out


def _regulators(b: _Board) -> list[Finding]:
    out = []
    for comp in b.c.components:
        if _kind(comp) != "linreg":
            continue
        ins, outs = b.pins_named(comp, _IN_PINS), b.pins_named(comp, _OUT_PINS)
        if not ins or not outs:
            continue
        n_in, n_out = b.net_of.get((comp.id, ins[0])), b.net_of.get((comp.id, outs[0]))
        v_in, v_out = b.v(n_in), b.v(n_out)
        name = f"{comp.reference} ({comp.value})"
        if v_in is None or v_out is None:
            continue
        head = v_in - v_out
        family = comp.value.upper()
        dropout = 2.0 if re.search(r"78\d\d", family) else 1.3 if "1117" in family else None
        if dropout is not None:
            sev = "fail" if head < dropout else "warn" if head < dropout + 0.5 else "ok"
            out.append(Finding("reg_dropout", sev, f"{name}: 입출력 전압 차 {head:g} V",
                               f"드롭아웃(일반값 약 {dropout:g} V)보다 {'작아 출력이 유지되지 않을 수 있습니다' if sev == 'fail' else '여유가 적습니다' if sev == 'warn' else '충분히 큽니다'}. "
                               "정확한 값은 데이터시트의 최대 드롭아웃으로 확인하세요.",
                               f"{v_in:g} V − {v_out:g} V = {head:g} V", (comp.id,)))
        per_100ma = head * 0.1
        fp = (comp.footprint_id or "").upper()
        thermal = ""
        if "TO-220" in fp:
            thermal = f"; 방열판 없는 TO-220 열저항 약 50 °C/W(ST L7805 데이터시트 값)이면 +{per_100ma * 50:.0f} °C"
        sev = "warn" if per_100ma >= 1.0 else "info"
        out.append(Finding("reg_heat", sev, f"{name}: 발열 100 mA당 {per_100ma:.2g} W",
                           "부하 전류를 회로 데이터에 적지 않아 100 mA 기준으로 계산했습니다. 실제 부하 전류로 다시 계산하고, "
                           "패키지 열저항으로 접합 온도를 확인하세요." + (" 방열 대책이 필요합니다." if sev == "warn" else ""),
                           f"P = (Vin − Vout) × I = ({v_in:g} − {v_out:g}) V × 0.1 A = {per_100ma:.2g} W{thermal}",
                           (comp.id,)))
    return out


def _decoupling(b: _Board) -> list[Finding]:
    out = []
    caps = [c for c in b.c.components if _kind(c) in ("c", "cp")]
    for comp in b.c.components:
        if len(comp.pins) < 3 or comp.reference.startswith(("J", "P", "CN", "X")):
            continue
        kind = _kind(comp)
        pins = b.pins_named(comp, _IN_PINS if kind == "linreg" else _POWER_PINS)
        if kind == "linreg":
            pins += b.pins_named(comp, _OUT_PINS)
        rails = sorted({n for p in pins if (n := b.net_of.get((comp.id, p))) and not is_ground_name(n)})
        for rail in rails:
            near = [c for c in caps if rail in b.two_nets(c) and any(is_ground_name(n) for n in b.two_nets(c) if n)]
            refs = ", ".join(sorted((c.reference for c in near), key=natural_key))
            what = "입출력 콘덴서" if kind == "linreg" else "디커플링 콘덴서"
            if near:
                out.append(Finding("decoupling", "ok", f"{comp.reference} {rail}: {what} 있음 ({refs})",
                                   "핀 가까이 배치하는지는 PCB에서 확인하세요.", None, (comp.id, *(c.id for c in near))))
            else:
                out.append(Finding("decoupling", "warn", f"{comp.reference} {rail}: {what} 없음",
                                   f"{rail}과 GND 사이 콘덴서가 없습니다. 데이터시트 응용 회로를 확인하세요.", None, (comp.id,)))
    return out


def _unknown_rails(b: _Board) -> list[Finding]:
    """Supply nets (on a connector or an IC power pin) whose voltage is not in the name: left out, said so."""
    names = set()
    for net, nodes in b.nodes.items():
        if is_ground_name(net) or b.v(net) is not None:
            continue
        for cid, pin in nodes:
            comp = b.comps.get(cid)
            if comp is None:
                continue
            pname = next((p.name for p in comp.pins if p.number == pin), None) or ""
            if comp.reference.startswith("J") or (len(comp.pins) >= 3 and _POWER_PINS.match(pname)):
                names.add(net)
    if not names:
        return []
    listed = ", ".join(sorted(names))
    return [Finding("unknown_voltage", "info", f"전압을 알 수 없는 전원 Net: {listed}",
                    "Net 이름에 전압이 없어(예: +5V, +3V3) 전압을 쓰는 계산에서 뺐습니다. 이름에 전압을 넣으면 검토됩니다.")]


def engineering_review(circuit: CircuitIR) -> list[Finding]:
    """All calculated checks, most severe first."""
    b = _Board(circuit)
    leds = _leds(b)
    seen = {c for f in leds if f.rule == "r_power" for c in f.components}
    findings = (_capacitors(b) + leds + _resistors_across_rails(b, seen) + _regulators(b) + _decoupling(b)
                + _unknown_rails(b))
    return sorted(findings, key=lambda f: (ORDER[f.severity], f.rule, f.title))


def review_summary(findings: list[Finding]) -> dict[str, int]:
    out = {k: 0 for k in ORDER}
    for f in findings:
        out[f.severity] += 1
    return out
