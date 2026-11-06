"""Calculated checks for blocks common in practice: relay drivers, MOSFET switches, optocoupler inputs, RS-485,
op-amp ADC inputs and USB-C power. Part of the technical review (``engineering.engineering_review``).

Like the rest of the review these are calculations to check, not a verification. Part figures (MOSFET gate
thresholds, optocoupler CTR grades) are typical datasheet values named in each finding; read the real datasheet.
Some symbols have unnamed pins (the PC817 optocoupler, the SANYOU SRD relay); their pin roles are listed here,
read from the KiCad 10.0.8 symbol drawings.
"""

from __future__ import annotations

import re

from .datasheets import is_ground_name
from .engineering import _POWER_PINS, Finding, _Board, _diode_rating, _fmt, _kind, _r_power, _vf
from .models import Component
from .parts import parse_value

# --- pin roles for symbols whose pins have no names -------------------------------------------------------

_PIN_ROLES: list[tuple[re.Pattern, dict[str, str]]] = [
    # Standard 4-pin phototransistor coupler: 1 anode, 2 cathode, 3 emitter, 4 collector.
    (re.compile(r"^Isolator:(PC817|LTV-817|EL817)"), {"1": "A", "2": "K", "3": "E", "4": "C"}),
    # Coil drawn between pins 2 and 5, the moving contact (COM) at pin 1; pins 3/4 are the two fixed contacts.
    (re.compile(r"^Relay:SANYOU_SRD_Form_C$"), {"2": "COIL", "5": "COIL", "1": "COM", "3": "CONTACT", "4": "CONTACT"}),
]


def roles(comp: Component) -> dict[str, list[str]]:
    """Role -> pin numbers: from the table for unnamed-pin symbols, else the pin names (upper case)."""
    out: dict[str, list[str]] = {}
    for rx, table in _PIN_ROLES:
        if rx.match(comp.symbol_id):
            for number, role in table.items():
                out.setdefault(role, []).append(number)
            return out
    for p in comp.pins:
        if p.name:
            out.setdefault(p.name.upper(), []).append(p.number)
    return out


def _net(b: _Board, comp: Component, role: str) -> str | None:
    pins = roles(comp).get(role) or []
    return b.net_of.get((comp.id, pins[0])) if pins else None


def _resistors_between(b: _Board, n1: str | None, n2_test) -> list[Component]:
    """Resistors with one end on ``n1`` and the other on a net for which ``n2_test(net)`` is true."""
    out = []
    for comp in b.c.components:
        if _kind(comp) != "r":
            continue
        a, c = b.two_nets(comp)
        if n1 in (a, c):
            other = c if a == n1 else a
            if other and n2_test(other):
                out.append(comp)
    return out


def _ohms(comp: Component) -> float | None:
    return parse_value(comp.value)


def _supply_net(b: _Board, net: str | None) -> bool:
    return bool(net) and not is_ground_name(net) and (b.v(net) or 0) > 0


def _rated_v(text: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*-?\s*V\s*DC|(\d+(?:\.\d+)?)VDC", text.upper())
    return float(m.group(1) or m.group(2)) if m else None


# --- relays ----------------------------------------------------------------------------------------------------


def _relays(b: _Board) -> list[Finding]:
    out = []
    for comp in b.c.components:
        coil = roles(comp).get("COIL") or []
        if len(coil) != 2:
            continue
        n1, n2 = (b.net_of.get((comp.id, p)) for p in coil)
        if not n1 or not n2:
            continue
        supply, switched = (n1, n2) if b.v(n1) is not None else (n2, n1) if b.v(n2) is not None else (None, None)
        name = f"{comp.reference} ({comp.value})"
        rated = _rated_v(f"{comp.value} {comp.manufacturer_part_number or ''}")
        if supply and rated:
            v = b.v(supply)
            if abs(v - rated) > 0.15 * rated:
                out.append(Finding("relay_coil", "fail", f"{name}: 코일 정격 {rated:g} V, 전원 {v:g} V",
                                   "코일 정격과 전원 전압이 다릅니다. 정격보다 높으면 코일이 타고, 낮으면 붙지 않습니다.",
                                   f"{supply} {v:g} V vs 정격 {rated:g} V", (comp.id,)))
            else:
                out.append(Finding("relay_coil", "ok", f"{name}: 코일 정격 {rated:g} V = 전원 {supply}",
                                   "코일 전류·접점 정격은 데이터시트로 확인하세요.", None, (comp.id,)))
        across = [c for c in b.c.components if c.id != comp.id and _kind(c) in ("diode", "zener")
                  and set(b.two_nets(c)) == {n1, n2}]
        if not across:
            out.append(Finding("relay_flyback", "fail", f"{name}: 플라이백 다이오드 없음",
                               "코일을 끄는 순간 역기전력(수십~수백 V)이 스위칭 소자에 걸립니다. 코일 양단에 다이오드를 두되 "
                               "캐소드를 전원 쪽으로 다세요.", None, (comp.id,)))
            continue
        for d in across:
            k_net = b.net_of.get((d.id, b.pin_by_name(d, "K") or ""))
            if _kind(d) == "zener":
                out.append(Finding("relay_flyback", "ok", f"{name}: 코일 양단 {d.reference}(제너·TVS)",
                                   "제너·TVS는 다이오드만 쓸 때보다 빨리 끕니다. 클램프 전압이 스위칭 소자 정격보다 낮은지 보세요.",
                                   None, (comp.id, d.id)))
            elif supply and k_net == supply:
                out.append(Finding("relay_flyback", "ok", f"{name}: 플라이백 다이오드 {d.reference} 방향 맞음",
                                   f"캐소드가 {supply}(전원) 쪽, 애노드가 {switched}(스위치) 쪽입니다.", None, (comp.id, d.id)))
            elif supply and k_net == switched:
                out.append(Finding("relay_flyback", "fail", f"{name}: 플라이백 다이오드 {d.reference} 방향 반대",
                                   f"애노드가 {supply}(전원) 쪽이라 스위치를 켜면 {d.reference}로 전원이 단락됩니다.",
                                   None, (comp.id, d.id)))
    return out


# --- MOSFET switches ------------------------------------------------------------------------------------------------

# (pattern on value/part number, channel, Vgs(th) max V, lowest Vgs with Rds(on) specified V, Vds max V). Typical
# datasheet figures; the finding says to confirm them.
_MOSFETS = [(r"AO3400", "N", 1.45, 2.5, 30.0), (r"IRLML2502", "N", 1.2, 2.5, 20.0), (r"2N7002", "N", 2.5, 4.5, 60.0)]


def _mosfets(b: _Board) -> list[Finding]:
    out = []
    for comp in b.c.components:
        r = roles(comp)
        if not all(k in r for k in ("G", "S", "D")) or len(comp.pins) > 8:
            continue
        g, s, d = (b.net_of.get((comp.id, r[k][0])) for k in ("G", "S", "D"))
        if not g or not s or not d:
            continue
        name = f"{comp.reference} ({comp.value})"
        text = f"{comp.value} {comp.manufacturer_part_number or ''} {comp.symbol_id}".upper()
        part = next((p for p in _MOSFETS if re.search(p[0], text)), None)
        channel = part[1] if part else ("N" if "NMOS" in text or "N-CH" in text else "P" if "PMOS" in text else None)
        if channel != "N" or not is_ground_name(s):
            continue  # low-side N-channel switches only for now
        pull = _resistors_between(b, g, lambda n, s=s: n == s)
        if pull:
            out.append(Finding("mosfet_gate", "ok", f"{name}: 게이트 풀다운 {pull[0].reference} ({pull[0].value})",
                               "구동 쪽이 꺼져 있거나 리셋 중(고임피던스)이어도 게이트가 0 V로 유지됩니다.", None,
                               (comp.id, pull[0].id)))
        else:
            out.append(Finding("mosfet_gate", "warn", f"{name}: 게이트 풀다운 없음",
                               "MCU 리셋 중이나 커넥터가 빠졌을 때 게이트가 떠서 MOSFET이 반쯤 켜질 수 있습니다. "
                               "게이트–소스 사이에 10~100 kΩ을 두세요.", None, (comp.id,)))
        # Drive level: the gate net's voltage, or the voltage behind a series resistor.
        drive, via = b.v(g), ""
        if drive is None:
            for rs in _resistors_between(b, g, lambda n: b.v(n) is not None and not is_ground_name(n)):
                other = next(n for n in b.two_nets(rs) if n != g)
                drive, via = b.v(other), f" ({rs.reference} 거쳐 {other})"
                break
        if drive is not None and part:
            _, _, vth, vspec, vds = part
            if drive < vth:
                sev, title = "fail", f"게이트 {drive:g} V < Vgs(th) 최대 {vth:g} V: 켜지지 않을 수 있음"
            elif drive < vspec:
                sev, title = "warn", f"게이트 {drive:g} V < Rds(on) 규정 {vspec:g} V: 완전히 켜지지 않을 수 있음"
            else:
                sev, title = "ok", f"게이트 {drive:g} V ≥ Rds(on) 규정 {vspec:g} V"
            out.append(Finding("mosfet_drive", sev, f"{name}: {title}",
                               f"{part[0]} 대표값: Vgs(th) 최대 {vth:g} V, Rds(on)이 정해진 가장 낮은 Vgs {vspec:g} V. "
                               "데이터시트로 확인하세요.", f"Vgs = {drive:g} V{via}", (comp.id,)))
        elif drive is not None:
            out.append(Finding("mosfet_drive", "info", f"{name}: 게이트 구동 {drive:g} V{via}",
                               "로직 레벨 MOSFET인지(데이터시트에 이 Vgs에서의 Rds(on)이 있는지) 확인하세요.", None, (comp.id,)))
        if part:
            loads = [b.v(n) for c in b.c.components if c.id != comp.id for n in _neighbours(b, c, d)]
            v_load = max((v for v in loads if v is not None), default=None)
            if v_load is not None:
                vds = part[4]
                sev = "warn" if v_load > 0.8 * vds else "ok"
                out.append(Finding("mosfet_vds", sev, f"{name}: Vds {v_load:g} V / 정격 {vds:g} V",
                                   "유도성 부하는 끌 때 전압이 튑니다. 플라이백 다이오드가 있으면 전원 + 다이오드 Vf로 묶입니다."
                                   if sev == "ok" else "정격의 80 %를 넘습니다. 더 높은 Vds 정격을 고르세요.",
                                   f"{v_load:g} / {vds:g} = {v_load / vds:.0%}", (comp.id,)))
    return out


def _neighbours(b: _Board, comp: Component, net: str) -> list[str]:
    """The nets on the other side of a load hanging on ``net`` (a 2-pin part or a relay coil)."""
    coil = roles(comp).get("COIL") or []
    pins = coil if len(coil) == 2 else ([p.number for p in comp.pins] if len(comp.pins) == 2 else [])
    nets = [b.net_of.get((comp.id, p)) for p in pins]
    if net not in nets or _kind(comp) in ("diode", "zener", "led", "c", "cp"):
        return []
    return [n for n in nets if n and n != net]


# --- optocouplers ------------------------------------------------------------------------------------------------

# CTR (%) by PC817 rank letter at If = 5 mA (minimum of the range); no letter: the full 50-600 % range.
_PC817_CTR = {"A": 80, "B": 130, "C": 200, "D": 300, "": 50}
_PC817_CTR_MAX = {"A": 160, "B": 260, "C": 400, "D": 600, "": 600}


def _walk(b: _Board, net: str | None, came_from: str, forward: bool,
          skip: frozenset[str] = frozenset()) -> tuple[list[tuple[Component, str]], str | None]:
    """From a coupler pin outwards through series two-pin parts (each net in between has exactly two nodes) to a net
    with a known voltage or to ground. Returns the parts with the pin they were entered by, and the end net.
    Parts in ``skip`` (a shunt resistor across the coupler's LED) are not part of the chain."""
    parts = []
    for _ in range(6):
        if net is None:
            return parts, None
        if is_ground_name(net) or b.vmax(net) is not None:
            return parts, net
        nodes = [(c, p) for c, p in b.nodes.get(net, []) if c != came_from and c not in skip]
        if len(nodes) != 1:
            return parts, None
        cid, pin = nodes[0]
        comp = b.comps[cid]
        if len(comp.pins) != 2:
            return parts, None
        parts.append((comp, pin))
        net = next((n for p, n in ((p.number, b.net_of.get((cid, p.number))) for p in comp.pins) if p != pin), None)
        came_from = cid
    return parts, None


def _optocouplers(b: _Board) -> list[Finding]:
    out = []
    for comp in b.c.components:
        r = roles(comp)
        if not all(k in r for k in ("A", "K", "E", "C")) or len(comp.pins) != 4:
            continue
        na, nk, ne, nc = (b.net_of.get((comp.id, r[k][0])) for k in ("A", "K", "E", "C"))
        name = f"{comp.reference} ({comp.value})"
        # A resistor straight across the LED (anode to cathode) takes current first and raises the turn-on voltage.
        shunts = [x for x in b.c.components if _kind(x) == "r" and na and nk and set(b.two_nets(x)) == {na, nk}]
        r_sh = _ohms(shunts[0]) if shunts else None
        skip = frozenset(x.id for x in shunts)
        up, src = _walk(b, na, comp.id, False, skip)
        down, ret = _walk(b, nk, comp.id, True, skip)
        if src is None or ret is None:
            out.append(Finding("opto_input", "info", f"{name}: 입력 전류를 계산하지 못함",
                               "LED 쪽 경로가 갈라지거나 전압을 아는 Net에 닿지 않습니다. 입력 Net의 전압(범위)을 적으세요.",
                               None, (comp.id,)))
            continue
        ohms, vf_lo, vf_hi, chain, bad = 0.0, 1.0, 1.4, [comp.reference + " LED 1.0~1.4 V"], []
        for part, entry, toward_source in [(p, e, True) for p, e in up] + [(p, e, False) for p, e in down]:
            kind = _kind(part)
            if kind == "r":
                ohms += _ohms(part) or 0.0
                chain.append(f"{part.reference} {part.value}")
            elif kind in ("diode", "led", "zener"):
                k_pin = part.pins[0].number if (part.pins[0].name or "").upper() == "K" else b.pin_by_name(part, "K")
                # Current flows from the source towards the coupler: walking up, a part is entered at its cathode.
                if (entry == k_pin) != toward_source:
                    bad.append(part)
                lo, hi = (_vf(part)[0] if kind == "led" else ((rating[3] - 0.3, rating[3]) if (rating := _diode_rating(part))
                                                             else (0.6, 1.0)))
                vf_lo, vf_hi = vf_lo + lo, vf_hi + hi
                chain.append(f"{part.reference} {lo:g}~{hi:g} V")
        if bad:
            out.append(Finding("opto_input", "fail", f"{name}: 입력 경로의 {', '.join(p.reference for p in bad)} 방향 반대",
                               "입력 전류가 흐르는 방향과 반대로 달려 있어 LED가 켜지지 않습니다.", None,
                               (comp.id, *(p.id for p in bad))))
            continue
        if ohms <= 0:
            out.append(Finding("opto_input", "fail", f"{name}: 입력에 직렬 저항 없음",
                               "LED 전류를 제한하는 저항이 없습니다.", None, (comp.id,)))
            continue
        v_hi, v_lo = b.vmax(src), b.vmin(src)
        r_hi = 0.0 if is_ground_name(ret) else (b.vmax(ret) or 0.0)
        r_lo = 0.0 if is_ground_name(ret) else (b.vmin(ret) or 0.0)
        # The series resistor carries the whole current; the LED gets what the shunt (at the LED's Vf) leaves.
        total_max = (v_hi - r_lo - vf_lo) / ohms
        i_min = (v_lo - r_hi - vf_hi) / ohms - (1.4 / r_sh if r_sh else 0.0)
        i_max = total_max - (1.0 / r_sh if r_sh else 0.0)
        calc = (f"I = ({src} {v_lo:g}~{v_hi:g} V − Vf {vf_lo:.1f}~{vf_hi:.1f} V) / {_fmt(ohms, 'Ω')}"
                + (f" − 분로 {shunts[0].reference} (1.4~1.0 V / {shunts[0].value})" if r_sh else "")
                + f" = {_fmt(max(i_min, 0), 'A')} ~ {_fmt(i_max, 'A')} ({' + '.join(chain)})")
        if i_min <= 0:
            out.append(Finding("opto_input", "fail", f"{name}: 최저 입력 {v_lo:g} V에서 켜지지 않음",
                               "직렬 Vf 합이 최저 입력보다 큽니다.", calc, (comp.id,)))
            continue
        sev = "fail" if i_max > 0.05 else "warn" if i_min < 0.001 or i_max > 0.02 else "ok"
        out.append(Finding("opto_input", sev, f"{name}: {'LED' if r_sh else '입력'} 전류 {_fmt(i_min, 'A')} ~ {_fmt(i_max, 'A')}",
                           {"fail": "LED 최대 정격(PC817 50 mA)을 넘습니다.",
                            "warn": "최저 입력에서 1 mA 미만이거나 최고 입력에서 20 mA를 넘습니다. 저항을 다시 고르세요.",
                            "ok": "최저~최고 입력 모두 LED 동작 범위(1~20 mA) 안입니다."}[sev], calc, (comp.id,)))
        for part, _ in up + down:
            if _kind(part) == "r":
                out += _r_power(part, total_max * total_max * (_ohms(part) or 0),
                                f"P = I² × R = ({_fmt(total_max, 'A')})² × {_fmt(_ohms(part) or 0, 'Ω')} (최고 입력 {v_hi:g} V)")
        # Output: collector pull-up against what the LED current can sink through the transistor.
        pull = _resistors_between(b, nc, lambda n: _supply_net(b, n))
        if pull and ne and is_ground_name(ne):
            vcc = b.v(next(n for n in b.two_nets(pull[0]) if n != nc))
            need = (vcc - 0.2) / (_ohms(pull[0]) or 1)
            m = re.search(r"PC817([A-D]?)", f"{comp.manufacturer_part_number or ''} {comp.value}".upper())
            if m:
                ctr = _PC817_CTR[m.group(1)]
                can = ctr / 100 * i_min
                sev = "ok" if can >= 2 * need else "warn" if can >= need else "fail"
                out.append(Finding("opto_ctr", sev, f"{name}: 출력 {_fmt(can, 'A')} 가능 / 필요 {_fmt(need, 'A')}",
                                   {"ok": "최저 입력에서도 CTR 최소값으로 2배 이상 여유가 있습니다(수명에 따른 CTR 저하 고려).",
                                    "warn": "포화는 되지만 여유가 2배 미만입니다. 오래 쓰면 CTR이 줄어 Low가 안 될 수 있습니다.",
                                    "fail": "최저 입력에서 출력 트랜지스터가 포화되지 않아 Low로 내려가지 않습니다."}[sev],
                                   f"CTR 최소 {ctr} %(PC817{m.group(1) or ''}, If 5 mA 기준) × {_fmt(i_min, 'A')} vs "
                                   f"({vcc:g} V − 0.2 V) / {pull[0].value}", (comp.id, pull[0].id)))
                # Turn-on threshold: the lowest input at which a coupler with the highest CTR already pulls low.
                ctr_hi = _PC817_CTR_MAX[m.group(1)]
                led_on = need / (ctr_hi / 100)
                v_on = vf_lo + r_lo + ohms * (led_on + (1.0 / r_sh if r_sh else 0.0))
                out.append(Finding("opto_threshold", "info", f"{name}: 입력 약 {v_on:.1f} V부터 출력이 켜질 수 있음",
                                   f"직렬 Vf 최소 합 {vf_lo:.1f} V를 넘으면 LED에 전류가 흐르고, CTR이 최대({ctr_hi} %)인 부품이면 "
                                   f"이 전압에서 출력이 Low가 됩니다. 「몇 V 이하에서 OFF」 요구가 있으면 그 전압이 이 값보다 낮은지 "
                                   "확인하세요. 문턱을 올리려면 LED 양단에 분로 저항을 두세요(낮은 전류의 CTR은 데이터시트 곡선으로 확인).",
                                   f"V = Vf 최소 {vf_lo:.1f} V + {_fmt(ohms, 'Ω')} × (출력 {_fmt(need, 'A')} / CTR {ctr_hi} %"
                                   + (f" + 분로 1.0 V / {shunts[0].value}" if r_sh else "") + ")",
                                   (comp.id, *(x.id for x in shunts))))
        grounds = {n for n in (ret, ne) if n and is_ground_name(n)}
        if ret and ne and is_ground_name(ret) and is_ground_name(ne):
            if ret == ne:
                out.append(Finding("opto_isolation", "warn", f"{name}: 입력·출력 GND가 같은 Net({ret})",
                                   "포토커플러를 쓰지만 양쪽 GND가 이어져 있어 절연되지 않습니다.", None, (comp.id,)))
            else:
                out.append(Finding("opto_isolation", "ok", f"{name}: 절연됨 ({' / '.join(sorted(grounds))})",
                                   "입력 쪽과 출력 쪽 GND가 다른 Net입니다. PCB에서 두 영역 사이 거리(연면거리)를 두세요.",
                                   None, (comp.id,)))
    return out


# --- RS-485 -----------------------------------------------------------------------------------------------------------


def _rs485(b: _Board) -> list[Finding]:
    out = []
    for comp in b.c.components:
        r = roles(comp)
        if not ("A" in r and "B" in r and ("DE" in r or "~{RE}" in r or "RE" in r)) or len(comp.pins) < 8:
            continue
        na, nb = _net(b, comp, "A"), _net(b, comp, "B")
        vcc = b.supply_of(comp.id)
        if not na or not nb:
            continue
        name = comp.reference
        term = _resistors_between(b, na, lambda n, nb=nb: n == nb)
        r_t = _ohms(term[0]) if term else None
        if r_t is not None:
            sev = "ok" if 100 <= r_t <= 130 else "warn"
            out.append(Finding("rs485_term", sev, f"{name}: 종단 저항 {term[0].reference} {term[0].value}",
                               "케이블 특성 임피던스(약 120 Ω)에 맞습니다. 버스 양 끝 노드에만 두세요." if sev == "ok"
                               else "보통 120 Ω(케이블 임피던스)입니다.", None, (comp.id, term[0].id)))
        else:
            out.append(Finding("rs485_term", "info", f"{name}: 종단 저항 없음",
                               "이 보드가 버스 끝단이 아니면 정상입니다. 끝단이면 A–B 사이에 120 Ω을 두세요.", None, (comp.id,)))
        up = _resistors_between(b, na, lambda n: _supply_net(b, n))
        down = _resistors_between(b, nb, is_ground_name)
        wrong = _resistors_between(b, nb, lambda n: _supply_net(b, n)) + _resistors_between(b, na, is_ground_name)
        if wrong:
            out.append(Finding("rs485_bias", "fail", f"{name}: 페일세이프 바이어스 방향 반대",
                               "A는 전원으로, B는 GND로 당겨야 유휴 상태가 '1'(마크)이 됩니다.", None,
                               (comp.id, *(w.id for w in wrong))))
        elif up and down and vcc:
            r_pu, r_pd = _ohms(up[0]) or 0, _ohms(down[0]) or 0
            one = r_t if r_t else 120.0
            both = 1 / (1 / one + 1 / 120.0) if r_t else 120.0
            v_one = vcc * one / (r_pu + one + r_pd)
            v_both = vcc * both / (r_pu + both + r_pd)
            r_need = (vcc * 60 / 0.25 - 60) / 2
            sev = "ok" if v_both >= 0.2 else "warn"
            out.append(Finding("rs485_bias", sev, f"{name}: 유휴 차동 전압 {v_both * 1000:.0f} mV (양 끝 종단)",
                               "표준 수신기 문턱(±200 mV)을 넘어 버스가 놓여도 수신이 '1'로 정해집니다." if sev == "ok" else
                               f"양 끝에 120 Ω 종단이 있으면 200 mV에 못 미칩니다(이 노드 종단만이면 {v_one * 1000:.0f} mV). "
                               f"바이어스를 각 {r_need:.0f} Ω 이하로 낮추거나, 버스 유휴 페일세이프가 있는 트랜시버를 쓰세요.",
                               f"V_AB = {vcc:g} V × {both:.0f} Ω / ({up[0].value} + {both:.0f} Ω + {down[0].value})",
                               (comp.id, up[0].id, down[0].id)))
        else:
            out.append(Finding("rs485_bias", "info", f"{name}: 페일세이프 바이어스 없음",
                               "버스가 놓였을 때 수신 출력이 정해지지 않습니다. 트랜시버에 버스 유휴 페일세이프가 있거나 다른 노드에 "
                               "바이어스가 있으면 괜찮습니다.", None, (comp.id,)))
        de = _net(b, comp, "DE")
        re_ = _net(b, comp, "~{RE}") or _net(b, comp, "RE")
        if de and de == re_:
            low = _resistors_between(b, de, is_ground_name)
            high = _resistors_between(b, de, lambda n: _supply_net(b, n))
            if low:
                out.append(Finding("rs485_dir", "ok", f"{name}: DE/RE 풀다운 {low[0].reference} → 리셋 중 수신 모드",
                                   "MCU가 핀을 잡기 전에도 버스를 점유하지 않습니다.", None, (comp.id, low[0].id)))
            elif high:
                out.append(Finding("rs485_dir", "warn", f"{name}: DE/RE 풀업 → 리셋 중 송신 모드",
                                   "MCU가 리셋되는 동안 이 노드가 버스를 잡아 다른 노드의 통신을 막습니다. 풀다운으로 바꾸세요.",
                                   None, (comp.id, high[0].id)))
            else:
                out.append(Finding("rs485_dir", "warn", f"{name}: DE/RE가 떠 있음",
                                   "MCU 리셋 중 DE/RE가 떠서 송신 모드가 되면 버스를 잡을 수 있습니다. 10 kΩ 풀다운을 두세요.",
                                   None, (comp.id,)))
    return out


# --- op-amp ADC inputs ------------------------------------------------------------------------------------------------


def _clamps(b: _Board, node: str, top: str) -> list[Component]:
    """Diodes that clamp ``node`` to ``top`` (anode on the node, cathode on the rail), a dual series diode included."""
    out = []
    for comp in b.c.components:
        if _kind(comp) not in ("diode", "zener"):
            continue
        r = roles(comp)
        if "COM" in r and "K" in r:  # BAT54S-style: common on the node, cathode to the rail
            if _net(b, comp, "COM") == node and _net(b, comp, "K") == top:
                out.append(comp)
        elif _net(b, comp, "A") == node and _net(b, comp, "K") == top:
            out.append(comp)
    return out


def _adc_inputs(b: _Board) -> list[Finding]:
    out = []
    for comp in b.c.components:
        r = roles(comp)
        if "+" not in r or "-" not in r:
            continue
        rail = next((b.net_of.get((comp.id, p)) for p in (r.get("V+") or r.get("VCC") or r.get("VDD") or [])), None)
        vs = b.v(rail)
        if vs is None:
            continue
        for pin in r["+"]:
            node = b.net_of.get((comp.id, pin))
            if not node:
                continue
            tops = _resistors_between(b, node, lambda n, rail=rail: b.vmax(n) is not None and not is_ground_name(n)
                                      and n != rail)
            bots = _resistors_between(b, node, is_ground_name)
            if not tops or not bots:
                continue
            rt, rb = _ohms(tops[0]) or 0, _ohms(bots[0]) or 0
            src = next(n for n in b.two_nets(tops[0]) if n != node)
            k = rb / (rt + rb) if rt + rb else 0
            v_nom, v_max = (b.v(src) or 0) * k, (b.vmax(src) or 0) * k
            name = f"{comp.reference} 입력({node})"
            calc = f"{src} × {bots[0].value} / ({tops[0].value} + {bots[0].value}) = ×{k:.3f}"
            out.append(Finding("adc_divider", "ok" if v_nom <= vs else "warn",
                               f"{name}: {src} {b.v(src):g} V → {v_nom:.2f} V (전원 {vs:g} V)",
                               "정상 입력 범위가 OP앰프·ADC 전원 안에 있습니다." if v_nom <= vs
                               else "정상 입력에서도 전원을 넘습니다. 분압비를 다시 고르세요.", calc, (comp.id, tops[0].id, bots[0].id)))
            if v_max > vs + 0.3:
                clamps = _clamps(b, node, rail)
                if clamps:
                    i = (b.vmax(src) - vs - 0.3) / rt if rt else 0
                    load = b.current(rail)
                    sev = "warn" if i > 0.005 or (load is not None and i > load[0]) else "info"
                    out.append(Finding("adc_clamp", sev, f"{name}: 최대 입력 {b.vmax(src):g} V에서 {clamps[0].reference}로 "
                                       f"{_fmt(i, 'A')} 클램프",
                                       f"클램프 전류가 {rail}로 흘러 들어갑니다. {rail}의 다른 부하가 이보다 작으면 레일 전압이 올라갈 수 "
                                       "있습니다(레귤레이터는 전류를 받아 주지 못함)."
                                       + (f" {rail} 부하 {_fmt(load[0], 'A')}보다 커서 레일이 올라갈 수 있습니다." if load and i > load[0] else "")
                                       + (" 5 mA를 넘으니 직렬 저항을 키우세요." if i > 0.005 else ""),
                                       f"I ≈ ({b.vmax(src):g} V − {vs:g} V − 0.3 V) / {tops[0].value}", (comp.id, clamps[0].id)))
                else:
                    out.append(Finding("adc_clamp", "fail", f"{name}: 최대 입력 {b.vmax(src):g} V에서 {v_max:.2f} V",
                                       f"OP앰프 입력 최대(전원 + 0.3 V = {vs + 0.3:g} V)를 넘는데 클램프 다이오드가 없습니다.",
                                       calc, (comp.id,)))
    return out


# --- USB-C power input ----------------------------------------------------------------------------------------------


def _usb_c(b: _Board) -> list[Finding]:
    out = []
    for comp in b.c.components:
        r = roles(comp)
        if "CC1" not in r or "CC2" not in r:
            continue
        c1, c2 = _net(b, comp, "CC1"), _net(b, comp, "CC2")
        name = comp.reference
        if c1 and c1 == c2:
            out.append(Finding("usb_cc", "fail", f"{name}: CC1·CC2가 한 Net",
                               "CC1과 CC2는 각각 5.1 kΩ으로 GND에 당겨야 합니다. 하나로 묶으면 충전기·케이블에 따라 인식되지 않습니다.",
                               None, (comp.id,)))
        else:
            for label, net in (("CC1", c1), ("CC2", c2)):
                pulls = _resistors_between(b, net, is_ground_name) if net else []
                if not pulls:
                    out.append(Finding("usb_cc", "fail", f"{name}: {label} 풀다운 없음",
                                       "싱크(전원을 받는 쪽)는 CC에 5.1 kΩ 풀다운이 있어야 USB-C 충전기·호스트가 VBUS를 켭니다.",
                                       None, (comp.id,)))
                    continue
                ohms = _ohms(pulls[0]) or 0
                ok = 4.6e3 <= ohms <= 5.6e3
                out.append(Finding("usb_cc", "ok" if ok else "warn", f"{name}: {label} 풀다운 {pulls[0].value}",
                                   "USB Type-C 싱크 표시(Rd = 5.1 kΩ)입니다." if ok else "싱크 표시는 5.1 kΩ(±10 %)입니다.",
                                   None, (comp.id, pulls[0].id)))
        vbus = _net(b, comp, "VBUS")
        if vbus:
            nets = {vbus} | {n for n, src in b.same_as.items() if src == b.same_as.get(vbus, vbus)} | {b.same_as.get(vbus, vbus)}
            caps = [c for c in b.c.components if _kind(c) in ("c", "cp")
                    and any(n in nets for n in b.two_nets(c)) and any(is_ground_name(n or "") for n in b.two_nets(c))]
            total = sum(parse_value(c.value) or 0 for c in caps)
            if total > 10.5e-6:
                out.append(Finding("usb_vbus_cap", "warn", f"{name}: VBUS 쪽 콘덴서 합 {_fmt(total, 'F')}",
                                   "USB 2.0 규격은 연결 순간 VBUS에 보이는 용량을 10 µF 이하로 정합니다(돌입 전류). 줄이거나 돌입 제한을 두세요.",
                                   " + ".join(f"{c.reference} {c.value}" for c in caps), tuple(c.id for c in caps)))
            elif caps:
                out.append(Finding("usb_vbus_cap", "ok", f"{name}: VBUS 쪽 콘덴서 합 {_fmt(total, 'F')}",
                                   "USB 2.0 규격(10 µF 이하) 안입니다.", " + ".join(f"{c.reference} {c.value}" for c in caps),
                                   tuple(c.id for c in caps)))
    return out


def block_rules(b: _Board) -> list[Finding]:
    return _relays(b) + _mosfets(b) + _optocouplers(b) + _rs485(b) + _adc_inputs(b) + _usb_c(b)


__all__ = ["block_rules", "roles", "_POWER_PINS"]
