"""Compare a component of the circuit with its datasheet summary. All decisions are code.

- :func:`pin_map_check`: datasheet pin table (one package) vs. the KiCad symbol's pins.
- :func:`ratings_check`: voltage of the net on each pin (read from the net name) vs. absolute maximum
  ratings and recommended operating conditions.
- :func:`external_parts_check`: parts the datasheet requires or recommends (decoupling capacitor,
  termination resistor ...) vs. what is actually connected between the named pins.

The results are automated evidence *if the summary is right*. They never change the circuit and
never set the engineering review status.
"""

from __future__ import annotations

import re
from collections import defaultdict

from .datasheets import DatasheetFacts, DsRating, is_ground_name, net_voltages, norm_pin_name, pin_names_match, to_volts
from .export import natural_key
from .models import CircuitIR, Component
from .parts import category_of, package_key, values_equal

PinNames = list[tuple[str, str]]
"""[(pin number, pin name)] of the component, from the KiCad library when available."""


def _pkg_size(text: str | None) -> str | None:
    key = package_key(text)
    if key is None or not key.known:
        return None
    return re.sub(r"^[DVUW](QFN|DFN)", r"\1", key.size)


def choose_package(facts: DatasheetFacts, component: Component, hints: list[str | None],
                   override: str | None = None) -> dict:
    """Which of the datasheet's packages this component uses.

    By pin count first (unique match), then by package name in the footprint / descriptions."""
    options = facts.packages()
    result = {"chosen": None, "options": options, "reason": ""}
    if override:
        if override in options:
            return {**result, "chosen": override, "reason": "사용자가 선택"}
        return {**result, "reason": f"선택한 패키지 '{override}'가 요약에 없습니다."}
    if not options:
        return {**result, "reason": "요약에 핀 표가 없습니다."}
    if len(options) == 1:
        return {**result, "chosen": options[0], "reason": "패키지가 하나뿐"}
    count = len(component.pins)
    by_count = [p for p in options if len(facts.pins_of(p)) == count]
    if len(by_count) == 1:
        return {**result, "chosen": by_count[0], "reason": f"핀 수 {count}개가 같은 패키지"}
    pool = by_count or options
    sizes = {s for s in (_pkg_size(h) for h in hints) if s}
    by_name = [p for p in pool if _pkg_size(p) in sizes]
    if len(by_name) == 1:
        return {**result, "chosen": by_name[0], "reason": "풋프린트·설명의 패키지 이름이 같음"}
    return {**result, "reason": f"패키지를 고를 수 없습니다 ({', '.join(pool)}). 풋프린트를 지정하거나 직접 고르세요."}


def pin_map_check(facts: DatasheetFacts, package: str | None, pin_names: PinNames) -> dict:
    if not package:
        return {"status": "unchecked", "rows": [], "counts": {}}
    ds = {p.number: p for p in facts.pins_of(package)}
    sym = dict(pin_names)
    rows = []
    # An exposed pad is often "EP" in the datasheet and a number in the symbol: pair them by name.
    ds_only = [n for n in ds if n not in sym]
    sym_only = [n for n in sym if n not in ds]
    pairs: dict[str, str] = {}
    for d in ds_only:
        if norm_pin_name(ds[d].name) == "EP" or norm_pin_name(d) == "EP":
            for s in sym_only:
                if norm_pin_name(sym[s]) == "EP" and s not in pairs.values():
                    pairs[d] = s
                    break
    for number in sorted(set(ds) | set(sym), key=natural_key):
        if number in pairs.values():
            continue
        d = ds.get(number)
        s_number = pairs.get(number, number)
        s_name = sym.get(s_number)
        if d is not None and s_name is not None:
            if not s_name or s_name == "~":
                status = "no_name"
            else:
                status = "match" if pin_names_match(s_name, d.name) else "mismatch"
        elif d is not None:
            status = "missing_in_symbol"
        else:
            status = "missing_in_datasheet"
        rows.append({"number": s_number if d is not None and s_name is not None else number,
                     "datasheet_number": d.number if d else None, "symbol_name": s_name,
                     "datasheet_name": d.name if d else None, "kind": d.kind if d else None,
                     "page": d.page if d else None, "status": status})
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    bad = sum(counts.get(k, 0) for k in ("mismatch", "missing_in_symbol", "missing_in_datasheet"))
    status = "mismatch" if bad else ("match" if counts.get("match") else "unchecked")
    return {"status": status, "rows": rows, "counts": counts}


def _pin_nets(circuit: CircuitIR) -> dict[tuple[str, str], str]:
    return {(node.component_id, node.pin): net.name for net in circuit.nets for node in net.nodes}


def _range(r: DsRating) -> tuple[float | None, float | None]:
    return to_volts(r.min, r.unit), to_volts(r.max, r.unit)


def ratings_check(facts: DatasheetFacts, circuit: CircuitIR, component: Component, pin_names: PinNames,
                  package: str | None = None) -> dict:
    nets = _pin_nets(circuit)
    volts = net_voltages(circuit)
    power_names = {norm_pin_name(p.name) for p in facts.pins_of(package or "") if p.kind and "전원" in p.kind}
    per_pin: dict[str, dict] = {}
    skipped = 0
    for group, entries in (("abs_max", facts.abs_max), ("recommended", facts.recommended)):
        for rating in entries:
            lo, hi = _range(rating)
            if lo is None and hi is None:
                continue  # not a voltage
            for listed in rating.pins:
                numbers = [n for n, name in pin_names if pin_names_match(name, listed)]
                if not numbers:
                    skipped += 1
                for number in numbers:
                    name = dict(pin_names)[number]
                    net = nets.get((component.id, number))
                    row = per_pin.setdefault(number, {
                        "number": number, "name": name, "net": net,
                        "voltage": volts.get(net) if net else None, "abs_max": [], "recommended": [],
                        "power": norm_pin_name(name) in power_names})
                    row[group].append({"parameter": rating.parameter, "min": lo, "max": hi,
                                       "condition": rating.condition, "page": rating.page})
    rows = []
    for number in sorted(per_pin, key=natural_key):
        row = per_pin[number]
        v = row["voltage"]
        if v is None:
            if not row["power"] and row["net"] is not None:
                skipped += 1
                continue
            row["status"] = "unconnected" if row["net"] is None else "unknown_voltage"
        elif any(_outside(v, r) for r in row["abs_max"]):
            row["status"] = "over_abs_max"
        elif any(_outside(v, r) for r in row["recommended"]):
            row["status"] = "outside_recommended"
        else:
            row["status"] = "ok"
        del row["power"]
        rows.append(row)
    statuses = {r["status"] for r in rows}
    status = ("error" if "over_abs_max" in statuses else "warning" if "outside_recommended" in statuses
              else "ok" if "ok" in statuses else "unchecked")
    return {"status": status, "rows": rows, "skipped": skipped}


def _outside(v: float, r: dict) -> bool:
    return (r["max"] is not None and v > r["max"] + 1e-9) or (r["min"] is not None and v < r["min"] - 1e-9)


_FAMILY = {"capacitor": {"capacitor", "capacitor_polarized"}, "capacitor_polarized": {"capacitor_polarized"}}


def external_parts_check(facts: DatasheetFacts, circuit: CircuitIR, component: Component,
                         pin_names: PinNames) -> dict:
    nets = _pin_nets(circuit)
    ground_nets = {n.name for n in circuit.nets if is_ground_name(n.name)}
    by_net: dict[str, set[str]] = defaultdict(set)  # net -> component ids
    for (cid, _pin), net in nets.items():
        by_net[net].add(cid)

    def resolve(endpoint: str) -> tuple[set[str] | None, str]:
        numbers = [n for n, name in pin_names if pin_names_match(name, endpoint)]
        if numbers:
            found = {nets[(component.id, n)] for n in numbers if (component.id, n) in nets}
            return found, "" if found else f"{endpoint} 핀이 연결되어 있지 않습니다."
        if is_ground_name(endpoint):
            return ground_nets, "" if ground_nets else "회로에 GND Net이 없습니다."
        return None, f"'{endpoint}'에 해당하는 핀이 없습니다."

    rows = []
    for part in facts.external_parts:
        row = {"requirement": part.requirement, "kind_label": part.kind_label, "value": part.value,
               "quantity": part.quantity, "endpoints": part.endpoints, "description": part.description,
               "page": part.page, "found": [], "status": "unchecked", "reason": ""}
        rows.append(row)
        if len(part.endpoints) != 2:
            row["reason"] = "연결이 '핀 ~ 핀' 형식이 아닙니다."
            continue
        if part.kind is None:
            row["reason"] = f"부품 종류 '{part.kind_label}'을(를) 알 수 없습니다."
            continue
        (a, why_a), (b, why_b) = resolve(part.endpoints[0]), resolve(part.endpoints[1])
        if a is None or b is None:
            row["reason"] = why_a or why_b
            continue
        if not a or not b:
            row["status"] = "missing"
            row["reason"] = why_a or why_b
            continue
        if a & b:
            row["status"] = "direct"
            row["reason"] = "두 지점이 부품 없이 직접 연결되어 있습니다. 데이터시트 설명과 맞는지 확인하세요."
            continue
        family = _FAMILY.get(part.kind, {part.kind})
        candidates = []
        for other in circuit.components:
            if other.id == component.id or category_of(other.symbol_id, other.reference) not in family:
                continue
            other_nets = [nets.get((other.id, p.number)) for p in other.pins]
            if any(n in a for n in other_nets) and any(n in b for n in other_nets):
                candidates.append(other)
        need = part.quantity or 1
        good = [c for c in candidates if part.value is None or values_equal(c.value, part.value)]
        row["found"] = [f"{c.reference} {c.value}" for c in sorted(candidates, key=lambda c: natural_key(c.reference))]
        if len(good) >= need:
            row["status"] = "present"
        elif good:
            row["status"] = "short"
            row["reason"] = f"{need}개 필요, {len(good)}개 있음"
        elif candidates:
            row["status"] = "value_differs"
            row["reason"] = f"값이 {part.value}가 아닙니다."
        else:
            row["status"] = "missing"
    missing_required = any(r["status"] in ("missing", "short", "value_differs") and r["requirement"] == "required"
                           for r in rows)
    missing_any = any(r["status"] in ("missing", "short", "value_differs") for r in rows)
    status = ("warning" if missing_required else "info" if missing_any
              else "ok" if any(r["status"] == "present" for r in rows) else "unchecked")
    return {"status": status, "rows": rows}


def check_component(circuit: CircuitIR, component: Component, facts: DatasheetFacts,
                    lib_pins: PinNames | None, hints: list[str | None], package: str | None = None) -> dict:
    """All three checks for one component. ``lib_pins`` comes from the KiCad library when the symbol
    is known; otherwise the IR's own pin names are used."""
    pin_names: PinNames = lib_pins if lib_pins is not None else [(p.number, p.name or "") for p in component.pins]
    chosen = choose_package(facts, component, [component.footprint_id, component.description, *hints], package)
    return {
        "package": chosen,
        "pin_names_from": "library" if lib_pins is not None else "circuit",
        "pin_map": pin_map_check(facts, chosen["chosen"], pin_names),
        "ratings": ratings_check(facts, circuit, component, pin_names, chosen["chosen"]),
        "external": external_parts_check(facts, circuit, component, pin_names),
    }
