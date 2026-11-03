"""Generate -> ERC -> netlist comparison -> SVG, writing every artefact to one folder.

Result wording follows plan section 10: the best this pipeline can report is
"automated checks passed". It never marks a design as verified.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from circuit_core import CircuitIR

from .cli import KiCadCli
from .erc import ErcReport
from .netlist import ConnectivityDiff, compare_with_ir, parse_kicad_netlist
from .schematic import GeneratedSchematic


@dataclass
class CheckReport:
    schematic: Path
    kicad: str
    erc: ErcReport
    connectivity: ConnectivityDiff
    svg: Path
    files: dict[str, str] = field(default_factory=dict)

    @property
    def automated_checks_passed(self) -> bool:
        return self.erc.automated_checks_passed and self.connectivity.ok

    def status_label(self) -> str:
        return "Automated checks passed" if self.automated_checks_passed else "Automated checks failed"

    def to_json(self) -> dict:
        return {
            "status": self.status_label(),
            "note": "ERC and netlist comparison are automated checks only; "
                    "the design is not engineer-reviewed.",
            "kicad": self.kicad,
            "erc": self.erc.summary(),
            "netlist_matches_ir": self.connectivity.ok,
            "netlist_diff": None if self.connectivity.ok else self.connectivity.describe(),
            "files": self.files,
        }


def run_checks(circuit: CircuitIR, generated: GeneratedSchematic, schematic: Path,
               cli: KiCadCli) -> CheckReport:
    out = schematic.parent
    version = cli.version()
    erc = cli.erc(schematic, out / "erc.json")
    netlist_path = out / f"{schematic.stem}.net"
    netlist = parse_kicad_netlist(cli.export_netlist(schematic, netlist_path))
    diff = compare_with_ir(circuit, netlist)
    svg = cli.export_svg(schematic, out)
    report = CheckReport(
        schematic=schematic,
        kicad=f"{version} ({cli.describe()})",
        erc=erc,
        connectivity=diff,
        svg=svg,
        files={
            "schematic": schematic.name,
            "erc": "erc.json",
            "netlist": netlist_path.name,
            "svg": svg.name,
        },
    )
    (out / "check_report.json").write_text(
        json.dumps(report.to_json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return report
