"""ADR-001 option A: Circuit IR -> SKiDL 3.0.0 -> schematizer 0.2.0 -> .kicad_sch (KiCad 10 target).

Run in a separate virtualenv (SKiDL is not a project dependency):

    uv venv skidl-venv && VIRTUAL_ENV=skidl-venv uv pip install skidl==3.0.0 schematizer==0.2.0
    KICAD10_SYMBOL_DIR=<KiCad>/share/kicad/symbols \
        skidl-venv/bin/python experiments/adr001_option_a/run_skidl.py \
        tests/fixtures/circuits/led_resistor_connector.json out/ ledA

Set AB_SEED=<int> to seed Python's global random module before generation.
"""

import json
import os
import random
import sys
from pathlib import Path


def main() -> None:
    ir_path, out_dir, top_name = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    out_dir.mkdir(parents=True, exist_ok=True)
    if os.environ.get("AB_SEED"):
        random.seed(int(os.environ["AB_SEED"]))

    from skidl import Net, Part, generate_schematic, reset, set_default_tool

    set_default_tool("kicad10")
    reset()
    ir = json.loads(ir_path.read_text(encoding="utf-8"))
    parts = {}
    for c in ir["components"]:
        lib, name = c["symbol_id"].split(":", 1)
        part = Part(lib, name, ref=c["reference"], value=c["value"], footprint=c.get("footprint_id") or "")
        parts[c["id"]] = part
        print(f"{c['reference']}: SKiDL pins {sorted(str(p.num) for p in part.pins)}")
    for n in ir["nets"]:
        net = Net(n["name"])
        for node in n["nodes"]:
            part = parts[node["component_id"]]
            pins = [p for p in part.pins if str(p.num) == node["pin"]]
            assert len(pins) == 1, (part.ref, node["pin"])
            net += pins[0]
    generate_schematic(tool="kicad10", filepath=str(out_dir), top_name=top_name, title=ir.get("title", ""))
    print("files:", sorted(str(f.relative_to(out_dir)) for f in out_dir.rglob("*") if f.is_file()))


if __name__ == "__main__":
    main()
