"""Technical review by calculation (plan section 10, level L3 pre-checks): no AI, only the circuit and the rules.

Each check states what it calculated and what it assumed, so a reviewer can redo it. Results are ``ok``,
``info``, ``warn`` or ``fail``. They point at things to look at; they do not verify a design (only an engineer's
review record does that, plan section 10.1).

Voltages come from the net's own ``voltage`` when the circuit has one, else from its name (``+5V``, ``+3V3``,
``GND``). Nets with neither are left out of voltage-based checks rather than guessed. Currents come from the nets'
``load_current``: a net without one takes the sum of what it feeds through linear regulators and series diodes.
Without any, the regulator heat is worked out per 100 mA.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .datasheets import is_ground_name, net_voltage, net_voltages
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
_POWER_PINS = re.compile(r"^(VCC|VDD|AVCC|AVDD|DVDD|VDDA|VDDIO|V\+|VS|VBAT|VIN|VI|IN|VM|VMOT|VBB)$", re.I)
_IN_PINS = re.compile(r"^(IN|VI|VIN)$", re.I)
_OUT_PINS = re.compile(r"^(OUT|VO|VOUT)$", re.I)
_SUPPLY_NAME = re.compile(r"PWR|POWER|SUPPLY|VIN|VCC|VDD|VBAT|VBUS|V\+|^\+", re.I)
_I2C = re.compile(r"(^|_)(I2C\d?_)?(SDA|SCL)\d?($|_)", re.I)
# Regulator families: (pattern on the value, typical dropout V, maximum output current A). Series values.
_REG_FAMILIES = [(r"78L\d\d", 1.7, 0.1), (r"78M\d\d", 2.0, 0.5), (r"78\d\d", 2.0, 1.5), (r"AMS1117", 1.3, 1.0),
                 (r"LD1117", 1.1, 0.8), (r"LM1117", 1.2, 0.8)]
# Brushed DC motor drivers: (pattern, VM min V, VM max V, peak output A, Rds(on) high + low side ohm, ILIM constant kV
# for I_TRIP = constant / R_ILIM, None without an ILIM pin). Typical datasheet values.
_MOTOR_DRIVERS = [(r"DRV8871", 6.5, 45.0, 3.6, 0.565, 64.0)]
# Rectifier and signal diodes by part number: (pattern, average forward current A, repetitive reverse V, typical Vf V).
_DIODES = [(r"1N400([1-7])", 1.0, {"1": 50, "2": 100, "3": 200, "4": 400, "5": 600, "6": 800, "7": 1000}, 1.0),
           (r"1N54(0[0-8])", 3.0, {"00": 50, "01": 100, "02": 200, "03": 300, "04": 400, "05": 500, "06": 600,
                                    "07": 800, "08": 1000}, 1.0),
           (r"1N581([7-9])", 1.0, {"7": 20, "8": 30, "9": 40}, 0.5),
           (r"1N582([0-2])", 3.0, {"0": 20, "1": 30, "2": 40}, 0.5),
           (r"SS1([2-6])", 1.0, {"2": 20, "3": 30, "4": 40, "5": 50, "6": 60}, 0.55),
           (r"SS3([2-6])", 3.0, {"2": 20, "3": 30, "4": 40, "5": 50, "6": 60}, 0.55),
           (r"1N4148|1N914", 0.15, {"": 100}, 1.0)]


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
    for scale, prefix in ((1e6, "M"), (1e3, "k"), (1.0, ""), (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"), (1e-12, "p")):
        if abs(v) >= scale * 0.9995 or scale == 1e-12:
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
    if lib == "Diode" or (lib == "Device" and (sym == "D" or sym.startswith(("D_SCHOTTKY", "D_SMALL")))):
        return "zener" if "ZENER" in sym or "TVS" in sym else "diode"
    if lib == "Device" and sym.startswith("CRYSTAL"):
        return "crystal"
    return "other"


def _diode_rating(comp: Component) -> tuple[str, float, float, float] | None:
    """(part, average current A, reverse voltage V, typical Vf V) for the common diodes, from the value or part number."""
    for text in (comp.value, comp.manufacturer_part_number or ""):
        for pattern, amps, volts, vf in _DIODES:
            m = re.search(pattern, text.upper())
            if m:
                key = m.group(1) if m.groups() else ""
                return m.group(0), amps, float(volts[key]), vf
    return None


class _Board:
    def __init__(self, circuit: CircuitIR):
        self.c = circuit
        self.net_of = {(n.component_id, n.pin): net.name for net in circuit.nets for n in net.nodes}
        self.nodes = {net.name: [(n.component_id, n.pin) for n in net.nodes] for net in circuit.nets}
        self.comps = {c.id: c for c in circuit.components}
        self.volts = net_voltages(circuit)
        self.nets = {n.name: n for n in circuit.nets}

    def v(self, net: str | None) -> float | None:
        return None if net is None else self.volts.get(net)

    def feeds(self, net: str) -> list[tuple[Component, str]]:
        """Parts that pass this net's current on to another net: a linear regulator (input here) and a series diode
        (anode here, conducting: anode not below the cathode)."""
        out = []
        for cid, pin in self.nodes.get(net, []):
            comp = self.comps[cid]
            kind = _kind(comp)
            if kind == "linreg" and pin in self.pins_named(comp, _IN_PINS):
                to = next((self.net_of.get((cid, p)) for p in self.pins_named(comp, _OUT_PINS)), None)
            elif kind == "diode" and pin == self.pin_by_name(comp, "A"):
                to = self.net_of.get((cid, self.pin_by_name(comp, "K") or ""))
                va, vk = self.v(net), self.v(to)
                if va is None or vk is None or va < vk - 0.5 or is_ground_name(to or ""):
                    to = None
            else:
                to = None
            if to and to != net:
                out.append((comp, to))
        return out

    def current(self, net: str | None, depth: int = 0) -> tuple[float, str] | None:
        """Current drawn from a net and how it was found: the net's ``load_current``, else the sum of what it feeds."""
        if net is None or depth > 6:
            return None
        spec = self.nets.get(net)
        if spec is not None and spec.load_current is not None:
            return spec.load_current, f"{net} 부하 {_fmt(spec.load_current, 'A')}(회로에 적은 값)"
        feeds = self.feeds(net)
        if not feeds:
            return None
        parts = [self.current(to, depth + 1) for _, to in feeds]
        if any(x is None for x in parts):
            return None
        total = sum(x[0] for x in parts)
        how = " + ".join(f"{comp.reference} → {to} {_fmt(x[0], 'A')}" for (comp, to), x in zip(feeds, parts, strict=True))
        return total, f"{net} 전류 = {how}"

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
        family = next(((d, m) for pat, d, m in _REG_FAMILIES if re.search(pat, comp.value.upper())), None)
        dropout, i_max = family if family else (None, None)
        if dropout is not None:
            sev = "fail" if head < dropout else "warn" if head < dropout + 0.5 else "ok"
            out.append(Finding("reg_dropout", sev, f"{name}: 입출력 전압 차 {head:g} V",
                               f"드롭아웃(일반값 약 {dropout:g} V)보다 {'작아 출력이 유지되지 않을 수 있습니다' if sev == 'fail' else '여유가 적습니다' if sev == 'warn' else '충분히 큽니다'}. "
                               "정확한 값은 데이터시트의 최대 드롭아웃으로 확인하세요.",
                               f"{v_in:g} V − {v_out:g} V = {head:g} V", (comp.id,)))
        to220 = "TO-220" in (comp.footprint_id or "").upper()
        load = b.current(n_out)
        if load is None:
            per_100ma = head * 0.1
            thermal = f"; 방열판 없는 TO-220 열저항 약 50 °C/W(L78 계열 데이터시트의 일반값)이면 +{per_100ma * 50:.0f} °C" if to220 else ""
            sev = "warn" if per_100ma >= 1.0 else "info"
            out.append(Finding("reg_heat", sev, f"{name}: 발열 100 mA당 {per_100ma:.2g} W",
                               f"부하 전류를 몰라 100 mA 기준으로 계산했습니다. 출력 Net({n_out})에 부하 전류를 적으면 실제 값으로 계산합니다. "
                               "패키지 열저항으로 접합 온도를 확인하세요." + (" 방열 대책이 필요합니다." if sev == "warn" else ""),
                               f"P = (Vin − Vout) × I = ({v_in:g} − {v_out:g}) V × 0.1 A = {per_100ma:.2g} W{thermal}",
                               (comp.id,)))
            continue
        amps, how = load
        watts = head * amps
        calc = f"P = (Vin − Vout) × I = ({v_in:g} − {v_out:g}) V × {_fmt(amps, 'A')} = {_fmt(watts, 'W')}; {how}"
        hot = False
        if to220:
            tj = 25 + watts * 50
            hot = tj > 125
            calc += f"; 방열판 없는 TO-220 약 50 °C/W, 주변 25 °C면 접합 약 {tj:.0f} °C(최대 125 °C, L78 계열 일반값)"
        sev = "warn" if watts >= 1.0 or hot else "ok" if watts < 0.5 else "info"
        detail = {"warn": "방열판이 필요합니다. 방열판 열저항으로 접합 온도를 다시 계산하세요."
                          + (" 방열판 없이는 접합 최대 온도를 넘습니다." if hot else ""),
                  "info": "손으로 만지기 어려울 만큼 뜨거워질 수 있습니다. 패키지 열저항으로 접합 온도를 확인하세요.",
                  "ok": "발열이 작습니다. 패키지 열저항으로 한 번 확인하세요."}[sev]
        out.append(Finding("reg_heat", sev, f"{name}: 발열 {_fmt(watts, 'W')} (출력 {_fmt(amps, 'A')})", detail, calc, (comp.id,)))
        if i_max is not None:
            over = amps > i_max
            out.append(Finding("reg_current", "fail" if over else "ok",
                               f"{name}: 출력 전류 {_fmt(amps, 'A')} / 최대 {_fmt(i_max, 'A')}",
                               ("시리즈 최대 출력 전류를 넘습니다. 더 큰 레귤레이터나 스위칭 방식을 고르세요." if over else
                                "시리즈 최대 출력 전류 안입니다. 실제 한계는 발열(접합 온도)로 더 낮아질 수 있습니다."),
                               f"{how}; 최대 전류는 시리즈 일반값, 데이터시트로 확인", (comp.id,)))
    return out


def _diodes(b: _Board) -> list[Finding]:
    """Series diodes (current, loss) and diodes reverse-biased between two known voltages (reverse voltage)."""
    out = []
    for comp in b.c.components:
        if _kind(comp) != "diode":
            continue
        a, k = b.pin_by_name(comp, "A"), b.pin_by_name(comp, "K")
        if a is None or k is None:
            continue
        na, nk = b.net_of.get((comp.id, a)), b.net_of.get((comp.id, k))
        va, vk = b.v(na), b.v(nk)
        if va is None or vk is None:
            continue
        rating = _diode_rating(comp)
        name = f"{comp.reference} ({comp.value})"
        part_note = f"{rating[0]} 일반값, 데이터시트로 확인" if rating else "품번으로 정격을 알 수 없음"
        if vk > va + 0.5:  # reverse-biased in normal operation
            vr = vk - va
            if rating is None:
                out.append(Finding("diode_reverse", "info", f"{name}: 역전압 {vr:g} V",
                                   "품번으로 정격을 알 수 없어 비교하지 않았습니다. 최대 역전압(VRRM)이 이보다 커야 합니다.",
                                   f"{nk} {vk:g} V − {na} {va:g} V = {vr:g} V", (comp.id,)))
                continue
            vrrm = rating[2]
            sev = "fail" if vr > vrrm else "warn" if vr > 0.8 * vrrm else "ok"
            out.append(Finding("diode_reverse", sev, f"{name}: 역전압 {vr:g} V / 정격 {vrrm:g} V",
                               {"fail": "최대 역전압을 넘습니다. 더 높은 정격의 다이오드를 고르세요.",
                                "warn": "최대 역전압의 80%를 넘습니다. 서지를 생각해 여유를 두세요.",
                                "ok": "최대 역전압 안입니다."}[sev],
                               f"{nk} {vk:g} V − {na} {va:g} V = {vr:g} V; VRRM {vrrm:g} V({part_note})", (comp.id,)))
            continue
        if is_ground_name(nk or "") or nk == na:
            continue
        load = b.current(nk)
        if load is None:
            continue
        amps, how = load
        vf = rating[3] if rating else 0.7
        watts = vf * amps
        if rating is None:
            out.append(Finding("diode_current", "info", f"{name}: 전류 {_fmt(amps, 'A')}, 손실 약 {_fmt(watts, 'W')}",
                               "품번으로 정격을 알 수 없어 비교하지 않았습니다.", f"{how}; P ≈ Vf × I, Vf 0.7 V로 가정", (comp.id,)))
            continue
        i_rated = rating[1]
        sev = "fail" if amps > i_rated else "warn" if amps > 0.7 * i_rated else "ok"
        out.append(Finding("diode_current", sev, f"{name}: 전류 {_fmt(amps, 'A')} / 정격 {_fmt(i_rated, 'A')}",
                           {"fail": "평균 순방향 전류 정격을 넘습니다.",
                            "warn": "정격의 70%를 넘습니다. 발열과 돌입 전류를 확인하세요.",
                            "ok": "정격 안입니다."}[sev] + f" 순방향 손실 약 {_fmt(watts, 'W')}, 전압 강하 약 {vf:g} V.",
                           f"{how}; P ≈ Vf × I = {vf:g} V × {_fmt(amps, 'A')}; {part_note}", (comp.id,)))
    return out


def _motor_drivers(b: _Board) -> list[Finding]:
    """Supply range, the current limit set by the ILIM resistor, and the H-bridge's conduction loss."""
    out = []
    for comp in b.c.components:
        family = next((f for f in _MOTOR_DRIVERS if re.search(f[0], f"{comp.value} {comp.symbol_id}", re.I)), None)
        if family is None:
            continue
        _, vmin, vmax, ipeak, rds, k_ilim = family
        name = f"{comp.reference} ({comp.value})"
        note = f"{family[0]} 데이터시트의 일반값, 원문으로 확인"
        vm_net = next((b.net_of.get((comp.id, p)) for p in b.pins_named(comp, re.compile(r"^VM$", re.I))), None)
        v = b.v(vm_net)
        if v is not None:
            sev = "fail" if v < vmin or v > vmax else "warn" if v > 0.9 * vmax or v < vmin * 1.1 else "ok"
            out.append(Finding("motor_vm", sev, f"{name}: VM {v:g} V / 범위 {vmin:g}~{vmax:g} V",
                               {"fail": "동작 전압 범위를 벗어납니다.",
                                "warn": "범위 끝에 가깝습니다. 모터 역기전력·전원 변동으로 넘을 수 있습니다.",
                                "ok": "동작 전압 범위 안입니다. 모터가 멈출 때 VM이 올라가는 것은 TVS·벌크 콘덴서로 막으세요."}[sev],
                               f"{vm_net} = {v:g} V; {note}", (comp.id,)))
        ilim = b.pin_by_name(comp, "ILIM")
        i_trip = None
        if ilim is not None and k_ilim is not None:
            net = b.net_of.get((comp.id, ilim))
            res = [b.comps[c] for c, _ in b.nodes.get(net or "", []) if c != comp.id and _kind(b.comps[c]) == "r"
                   and any(is_ground_name(n or "") for n in b.two_nets(b.comps[c]))]
            ohms = parse_value(res[0].value) if res else None
            if not ohms:
                out.append(Finding("motor_ilim", "warn", f"{name}: 전류 제한 저항을 찾지 못함",
                                   "ILIM 핀과 GND 사이 저항이 전류 제한을 정합니다. 없거나 값이 없으면 제한이 동작하지 않습니다.",
                                   None, (comp.id,)))
            else:
                i_trip = k_ilim / (ohms / 1000)
                sev = "warn" if i_trip > ipeak else "ok"
                out.append(Finding("motor_ilim", sev, f"{name}: 전류 제한 {_fmt(i_trip, 'A')} (R {res[0].reference} {res[0].value})",
                                   "모터 기동·구속 전류가 이 값에서 묶입니다." if sev == "ok" else
                                   f"최대 출력 전류({ipeak:g} A)보다 높게 잡혀 제한이 보호 역할을 못 합니다.",
                                   f"I_TRIP = {k_ilim:g} / R(kΩ) = {k_ilim:g} / {ohms / 1000:g} = {_fmt(i_trip, 'A')}; {note}",
                                   (comp.id, res[0].id)))
        load = b.current(vm_net)
        if load is not None:
            amps, how = load
            watts = amps * amps * rds
            sev = "warn" if watts >= 1.5 else "info" if watts >= 0.75 else "ok"
            calc = f"P = I² × Rds(on) = ({_fmt(amps, 'A')})² × {rds:g} Ω = {_fmt(watts, 'W')}; {how}; Rds(on) 상·하단 합 {note}"
            if i_trip is not None:
                calc += f"; 구속 시 {_fmt(i_trip, 'A')}면 {_fmt(i_trip * i_trip * rds, 'W')}(짧은 시간)"
            out.append(Finding("motor_heat", sev, f"{name}: 출력 손실 {_fmt(watts, 'W')} (모터 {_fmt(amps, 'A')})",
                               {"warn": "방열 패드의 구리 면적과 열 비아가 충분해야 합니다. 과열 보호로 꺼질 수 있습니다.",
                                "info": "방열 패드를 넓은 GND 구리에 붙이고 열 비아를 두세요.",
                                "ok": "손실이 작습니다. 방열 패드는 GND 구리에 붙이세요."}[sev], calc, (comp.id,)))
    return out


def _i2c_pullups(b: _Board) -> list[Finding]:
    out = []
    for net in sorted(b.nodes, key=natural_key):
        if not _I2C.search(net):
            continue
        pulls = []
        for cid, _ in b.nodes[net]:
            r = b.comps[cid]
            if _kind(r) != "r":
                continue
            far = next((n for n in b.two_nets(r) if n != net), None)
            v = b.v(far)
            if far and v is not None and v > 0:
                pulls.append(f"{r.reference} {r.value} → {far}")
        if pulls:
            out.append(Finding("i2c_pullup", "ok", f"{net}: 풀업 있음", ", ".join(pulls) + ". 값은 버스 속도와 배선 용량으로 확인하세요.",
                               None, tuple(c for c, _ in b.nodes[net] if _kind(b.comps[c]) == "r")))
        else:
            out.append(Finding("i2c_pullup", "warn", f"{net}: 풀업 저항 없음",
                               "I2C는 오픈 드레인이라 SDA·SCL마다 전원으로 가는 풀업 저항(보통 2.2~10 kΩ)이 필요합니다. "
                               "연결되는 다른 보드에 있으면 무시하세요."))
    return out


def _crystals(b: _Board) -> list[Finding]:
    out = []
    for comp in b.c.components:
        if _kind(comp) != "crystal":
            continue
        name = f"{comp.reference} ({comp.value})"
        ends = [p.number for p in comp.pins if (p.name or p.number).upper() not in ("GND", "2_GND", "4_GND")][:2]
        caps, missing = [], []
        for pin in ends:
            net = b.net_of.get((comp.id, pin))
            found = [c for c, _ in b.nodes.get(net or "", []) if _kind(b.comps[c]) in ("c", "cp")
                     and any(is_ground_name(n or "") for n in b.two_nets(b.comps[c]))]
            if found:
                caps.append(b.comps[found[0]])
            else:
                missing.append(f"{pin}번({net or '미연결'})")
        if missing:
            out.append(Finding("crystal_caps", "warn", f"{name}: 부하 콘덴서 없음 ({', '.join(missing)})",
                               "크리스탈 양 끝에서 GND로 가는 부하 콘덴서가 보통 필요합니다. 발진 IC와 크리스탈 데이터시트를 확인하세요.",
                               None, (comp.id,)))
            continue
        c1, c2 = (parse_value(c.value.split("/")[0]) for c in caps)
        calc = None
        if c1 and c2:
            series = c1 * c2 / (c1 + c2)
            calc = (f"CL ≈ C1·C2/(C1+C2) + 배선 약 3~5 pF = {_fmt(series, 'F')} + 3~5 pF = "
                    f"{_fmt(series + 3e-12, 'F')} ~ {_fmt(series + 5e-12, 'F')}")
        out.append(Finding("crystal_caps", "ok", f"{name}: 부하 콘덴서 있음 ({caps[0].reference}, {caps[1].reference})",
                           "크리스탈 데이터시트의 부하 용량(CL)과 비교하세요.", calc, (comp.id, caps[0].id, caps[1].id)))
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


def _is_supply(b: _Board, net: str) -> bool:
    """A supply net: on an IC power pin or a regulator, or a connector net that looks like power (a supply-like
    name, or a diode or a capacitor to ground on it). Connector signals (TXD, SDA) are not supplies."""
    if is_ground_name(net):
        return False
    on_connector = False
    for cid, pin in b.nodes.get(net, []):
        comp = b.comps[cid]
        kind = _kind(comp)
        pname = next((p.name for p in comp.pins if p.number == pin), None) or ""
        if len(comp.pins) >= 3 and (_POWER_PINS.match(pname) or (kind == "linreg" and _OUT_PINS.match(pname))):
            return True
        if comp.reference.startswith("J"):
            on_connector = True
        elif kind in ("diode", "zener") or (kind in ("c", "cp") and any(is_ground_name(n or "") for n in b.two_nets(comp))):
            if on_connector or any(b.comps[c].reference.startswith("J") for c, _ in b.nodes[net]):
                return True
    return on_connector and bool(_SUPPLY_NAME.search(net))


def _unknown_rails(b: _Board) -> list[Finding]:
    """Supply nets whose voltage is neither in the name nor written: left out, said so."""
    names = {net for net in b.nodes if b.v(net) is None and _is_supply(b, net)}
    if not names:
        return []
    listed = ", ".join(sorted(names))
    return [Finding("unknown_voltage", "info", f"전압을 알 수 없는 전원 Net: {listed}",
                    "Net 이름에 전압이 없고(예: +5V, +3V3) 전압 값도 적혀 있지 않아 전압을 쓰는 계산에서 뺐습니다. "
                    "「검토」 탭의 전원 조건에 전압을 적으면 검토됩니다.")]


def supply_nets(circuit: CircuitIR) -> list[dict]:
    """The nets whose voltage and load current the review uses, for a person to fill in: nets with a voltage (in the
    name or written), and nets on a connector, an IC power pin or a regulator."""
    b = _Board(circuit)
    out = []
    for net in sorted(b.nodes, key=natural_key):
        if is_ground_name(net):
            continue
        spec = b.nets[net]
        if not (_is_supply(b, net) or b.v(net) is not None or spec.load_current is not None):
            continue
        derived = None if spec.load_current is not None else b.current(net)
        out.append({"net": net, "voltage": spec.voltage, "voltage_from_name": net_voltage(net),
                    "load_current": spec.load_current, "derived_current": derived[0] if derived else None})
    return out


def engineering_review(circuit: CircuitIR) -> list[Finding]:
    """All calculated checks, most severe first."""
    b = _Board(circuit)
    leds = _leds(b)
    seen = {c for f in leds if f.rule == "r_power" for c in f.components}
    findings = (_capacitors(b) + leds + _resistors_across_rails(b, seen) + _regulators(b) + _diodes(b) + _decoupling(b)
                + _i2c_pullups(b) + _crystals(b) + _motor_drivers(b) + _unknown_rails(b))
    return sorted(findings, key=lambda f: (ORDER[f.severity], f.rule, f.title))


def review_summary(findings: list[Finding]) -> dict[str, int]:
    out = {k: 0 for k in ORDER}
    for f in findings:
        out[f.severity] += 1
    return out
