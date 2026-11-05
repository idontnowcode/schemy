"""Before/after comparison of two Circuit IRs, computed by code (plan v1.2 section 1.1).

The approval screen shows this diff, never an AI's description of its own change.
Components are matched by stable id; connections are compared per pin.
"""

from __future__ import annotations

from .export import natural_key
from .models import CircuitIR

FIELDS = ("reference", "value", "symbol_id", "footprint_id", "manufacturer", "manufacturer_part_number",
          "description")


def _pin_nets(circuit: CircuitIR | None) -> dict[tuple[str, str], str]:
    if circuit is None:
        return {}
    return {(n.component_id, n.pin): net.name for net in circuit.nets for n in net.nodes}


def diff_circuits(before: CircuitIR | None, after: CircuitIR) -> dict:
    b_comps = {c.id: c for c in before.components} if before else {}
    a_comps = {c.id: c for c in after.components}
    ref_of = {**{k: v.reference for k, v in b_comps.items()}, **{k: v.reference for k, v in a_comps.items()}}

    def comp_summary(c) -> dict:
        return {"id": c.id, "reference": c.reference, "value": c.value, "symbol_id": c.symbol_id,
                "footprint_id": c.footprint_id, "manufacturer_part_number": c.manufacturer_part_number,
                "origin": c.origin}

    added = [comp_summary(a_comps[k]) for k in a_comps if k not in b_comps]
    removed = [comp_summary(b_comps[k]) for k in b_comps if k not in a_comps]
    changed = []
    for k in a_comps.keys() & b_comps.keys():
        old, new = b_comps[k], a_comps[k]
        for f in FIELDS:
            if getattr(old, f) != getattr(new, f):
                changed.append({"id": k, "reference": new.reference, "field": f,
                                "before": getattr(old, f), "after": getattr(new, f)})
        old_pins = {p.number: p for p in old.pins}
        new_pins = {p.number: p for p in new.pins}
        for num in sorted(new_pins.keys() | old_pins.keys(), key=natural_key):
            o, n = old_pins.get(num), new_pins.get(num)
            if o is None or n is None:
                changed.append({"id": k, "reference": new.reference, "field": f"pin {num}",
                                "before": "있음" if o else "없음", "after": "있음" if n else "없음"})
            elif o.no_connect != n.no_connect:
                changed.append({"id": k, "reference": new.reference, "field": f"pin {num} no_connect",
                                "before": str(o.no_connect).lower(), "after": str(n.no_connect).lower()})

    b_nets, a_nets = _pin_nets(before), _pin_nets(after)
    pin_name = {}
    for comps in (b_comps, a_comps):
        for c in comps.values():
            for p in c.pins:
                pin_name[(c.id, p.number)] = p.name
    connections = []
    for key in b_nets.keys() | a_nets.keys():
        if b_nets.get(key) != a_nets.get(key):
            comp_id, pin = key
            connections.append({"reference": ref_of.get(comp_id, comp_id), "pin": pin, "pin_name": pin_name.get(key),
                                "before": b_nets.get(key), "after": a_nets.get(key)})
    connections.sort(key=lambda r: (natural_key(r["reference"]), natural_key(r["pin"])))

    b_names = {n.name for n in before.nets} if before else set()
    a_names = {n.name for n in after.nets}
    b_layout = before.layout if before else {}
    moved = sorted((ref_of.get(k, k) for k in after.layout.keys() | b_layout.keys()
                    if after.layout.get(k) != b_layout.get(k)), key=natural_key)
    b_pages = before.page_of() if before else {}
    a_pages = after.page_of()
    page_moves = [{"reference": a_comps[k].reference, "before": b_pages[k], "after": a_pages[k]}
                  for k in a_pages if k in b_pages and b_pages[k] != a_pages[k]]

    def target(note) -> str:
        if note.net:
            return f"net {note.net}"
        refs = [ref_of.get(c, c) for c in note.components]
        return ", ".join(sorted(refs, key=natural_key)) if refs else "page"

    b_notes = {n.id: n for n in before.notes} if before else {}
    a_notes = {n.id: n for n in after.notes}
    notes = []
    for k in [*b_notes, *(k for k in a_notes if k not in b_notes)]:
        old_n, new_n = b_notes.get(k), a_notes.get(k)
        if old_n == new_n:
            continue
        notes.append({"id": k, "target": target(new_n or old_n),
                      "before": old_n.text if old_n else None, "after": new_n.text if new_n else None})

    b_dec = {d.id: d for d in before.decisions} if before else {}
    a_dec = {d.id: d for d in after.decisions}
    decisions = []
    for k in [*b_dec, *(k for k in a_dec if k not in b_dec)]:
        old_d, new_d = b_dec.get(k), a_dec.get(k)
        if old_d == new_d:
            continue
        d = new_d or old_d
        decisions.append({"id": k, "topic": d.topic, "by": d.by,
                          "before": old_d.choice if old_d else None, "after": new_d.choice if new_d else None,
                          "reason": new_d.reason if new_d else None})

    # Voltages and load currents written on nets (for the technical review), by net name.
    b_spec = {n.name: n for n in before.nets} if before else {}
    net_specs = []
    for net in sorted(after.nets, key=lambda n: natural_key(n.name)):
        old_net = b_spec.get(net.name)
        for f in ("voltage", "load_current"):
            was = getattr(old_net, f) if old_net else None
            if was != getattr(net, f):
                net_specs.append({"net": net.name, "field": f, "before": was, "after": getattr(net, f)})

    changed.sort(key=lambda r: (natural_key(r["reference"]), r["field"]))
    added.sort(key=lambda r: natural_key(r["reference"]))
    removed.sort(key=lambda r: natural_key(r["reference"]))
    summary = {"components_added": len(added), "components_removed": len(removed),
               "properties_changed": len(changed), "connections_changed": len(connections),
               "nets_added": len(a_names - b_names), "nets_removed": len(b_names - a_names),
               "moved": len(moved), "page_moves": len(page_moves), "notes_changed": len(notes),
               "decisions_changed": len(decisions), "net_specs_changed": len(net_specs)}
    return {
        "summary": summary,
        "is_new_circuit": before is None,
        "components_added": added,
        "components_removed": removed,
        "properties_changed": changed,
        "connections": connections,
        "nets_added": sorted(a_names - b_names, key=natural_key),
        "nets_removed": sorted(b_names - a_names, key=natural_key),
        "moved": moved,
        "page_moves": page_moves,
        "notes": notes,
        "decisions": decisions,
        "net_specs": net_specs,
        "title_changed": None if before is None or before.title == after.title else
        {"before": before.title, "after": after.title},
    }
