"""English notes on the schematic (IR 0.5.0): rules, patch operations, diff, drawing, templates, AI design."""

import pytest
from pydantic import TypeAdapter, ValidationError

from circuit_core import CircuitIR, validate_circuit
from circuit_core.diff import diff_circuits
from circuit_core.export import circuit_markdown
from circuit_core.patch import PatchError, PatchOp, apply_patch
from kicad_adapter import generate_schematic, sexpr
from kicad_adapter.drawing import note_rect, overlaps
from kicad_adapter.sexpr import child, children, unquote

OPS = TypeAdapter(list[PatchOp])


def ops(*raw):
    return OPS.validate_python(list(raw))


def with_notes(circuit: CircuitIR, *notes: dict) -> CircuitIR:
    data = circuit.model_dump(mode="json")
    data["notes"] = list(notes)
    return CircuitIR.model_validate(data)


BLOCK = {"id": "note-led", "text": "LED, ~10 mA", "components": ["res-001", "led-001"]}
NET = {"id": "note-vin", "text": "5V from the header", "net": "VIN"}


def test_note_rules(led_circuit):
    ok = with_notes(led_circuit, BLOCK, NET)
    assert validate_circuit(ok).errors == []
    bad = with_notes(led_circuit, {"id": "a", "text": "x", "components": ["nope"]},
                     {"id": "a", "text": "y", "net": "NOPE"},
                     {"id": "b", "text": "z", "components": ["res-001"], "net": "VIN"})
    codes = sorted(i.error_code for i in validate_circuit(bad).errors)
    assert codes == ["DUPLICATE_NOTE_ID", "NOTE_TWO_TARGETS", "NOTE_UNKNOWN_COMPONENT", "NOTE_UNKNOWN_NET"]
    with pytest.raises(ValidationError):  # one line of text
        with_notes(led_circuit, {"id": "c", "text": "two\nlines"})


def test_set_and_remove_note_check_the_current_text(led_circuit):
    after = apply_patch(led_circuit, ops({"op": "set_note", "note": BLOCK}))
    assert after.notes[0].text == "LED, ~10 mA"
    with pytest.raises(PatchError) as exc:  # it exists now: a change must state its current text
        apply_patch(after, ops({"op": "set_note", "note": {**BLOCK, "text": "LED, ~5 mA"}}))
    assert exc.value.failures[0].code == "PRECONDITION_FAILED"
    changed = apply_patch(after, ops({"op": "set_note", "note": {**BLOCK, "text": "LED, ~5 mA"},
                                      "expect_text": "LED, ~10 mA"}))
    assert [n.text for n in changed.notes] == ["LED, ~5 mA"]
    gone = apply_patch(changed, ops({"op": "remove_note", "note_id": "note-led", "expect_text": "LED, ~5 mA"}))
    assert gone.notes == ()
    with pytest.raises(PatchError) as exc:
        apply_patch(led_circuit, ops({"op": "set_note", "note": {**NET, "net": "NOPE"}}))
    assert exc.value.failures[0].code == "UNKNOWN_NET"


def test_notes_follow_removed_parts_and_renamed_nets(led_circuit):
    base = with_notes(led_circuit, BLOCK, NET, {"id": "note-r", "text": "Series resistor", "components": ["res-001"]})
    after = apply_patch(base, ops({"op": "remove_component", "component_id": "res-001", "expect_reference": "R1"},
                                  {"op": "rename_net", "net": "VIN", "new_name": "VBUS"}))
    notes = {n.id: n for n in after.notes}
    assert notes["note-led"].components == ("led-001",)  # still has a part to stand next to
    assert "note-r" not in notes  # its only part is gone
    assert notes["note-vin"].net == "VBUS"
    assert validate_circuit(after).errors == []


def test_diff_and_markdown_show_notes(led_circuit):
    after = with_notes(led_circuit, BLOCK, NET)
    d = diff_circuits(led_circuit, after)
    assert d["summary"]["notes_changed"] == 2
    assert {(n["target"], n["after"]) for n in d["notes"]} == {("D1, R1", "LED, ~10 mA"),
                                                               ("net VIN", "5V from the header")}
    md = circuit_markdown(after)
    assert "## 회로도 주석" in md and "| D1, R1 | LED, ~10 mA |" in md and "| Net VIN | 5V from the header |" in md


def test_notes_are_drawn_as_italic_text_clear_of_the_drawing(led_circuit, library):
    circuit = with_notes(led_circuit, BLOCK, NET, {"id": "note-page", "text": "Demo board, not reviewed"})
    g = generate_schematic(circuit, library)
    doc = sexpr.parse(g.text)
    texts = {unquote(t[1]): t for t in children(doc, "text")}
    assert set(texts) == {"LED, ~10 mA", "5V from the header", "Demo board, not reviewed"}
    font = child(child(texts["LED, ~10 mA"], "effects"), "font")
    assert child(font, "italic") == ["italic", "yes"]
    rects = {k: note_rect((float(child(t, "at")[1]), float(child(t, "at")[2])), k) for k, t in texts.items()}
    pins = [(a.x, a.y) for a in g.anchors]
    for name, rect in rects.items():
        assert not any(rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3] for x, y in pins), name
    assert not any(overlaps(a, b) for a in rects.values() for b in rects.values() if a is not b)
    # Notes change nothing else: the same file apart from the text items.
    plain = generate_schematic(led_circuit, library).text
    assert sexpr.parse(plain)[:7] == doc[:7]


def test_template_caption_becomes_a_note(library):
    from circuit_core.templates import TEMPLATES, as_circuit, instantiate

    def pins_of(symbol_id):
        return [(p.number, p.name) for p in library.get(symbol_id).pins]

    inst = instantiate(TEMPLATES["mcu-atmega328p-min"], {"crystal": "16MHz"}, None, pins_of)
    assert inst.note.text == "MCU core: ATmega328P, 16MHz crystal"
    assert set(inst.note.components) == {c.id for c in inst.components}
    circuit = as_circuit(inst, "demo")
    assert circuit.notes == (inst.note,) and validate_circuit(circuit).errors == []


def test_ai_schematic_notes_map_to_parts_and_nets():
    from ai_engine.design import _schematic_notes

    components = [{"id": "u1", "reference": "U1"}, {"id": "r1", "reference": "R1"}]
    nets = [{"name": "OUT"}]
    raw = {"schematic_notes": [
        {"text": "Timer, ~1 Hz", "refs": ["U1", "r1"], "net": ""},
        {"text": "Timer output", "refs": [], "net": "OUT"},
        {"text": "Unknown part", "refs": ["Q9"], "net": ""},
        {"text": "Unknown net", "refs": [], "net": "NOPE"},
        {"text": "  ", "refs": ["U1"], "net": ""},
    ]}
    notes = _schematic_notes(raw, components, nets, [])
    assert notes == [{"id": "note-001", "text": "Timer, ~1 Hz", "components": ["u1", "r1"], "net": None},
                     {"id": "note-002", "text": "Timer output", "components": [], "net": "OUT"}]
