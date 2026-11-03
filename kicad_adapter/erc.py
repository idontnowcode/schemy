"""Parse ``kicad-cli sch erc --format json`` reports.

An ERC pass only means KiCad's automated rules found nothing to report. It is
not an electrical design review (plan section 10, level L2 only).
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ErcViolation:
    severity: str
    type: str
    description: str
    items: tuple[str, ...]


@dataclass(frozen=True)
class ErcReport:
    kicad_version: str
    violations: tuple[ErcViolation, ...]
    raw: dict

    @property
    def errors(self) -> list[ErcViolation]:
        return [v for v in self.violations if v.severity == "error"]

    @property
    def warnings(self) -> list[ErcViolation]:
        return [v for v in self.violations if v.severity == "warning"]

    @property
    def automated_checks_passed(self) -> bool:
        """No ERC errors. Warnings are reported but do not fail the check."""
        return not self.errors

    def summary(self) -> dict:
        return {
            "kicad_version": self.kicad_version,
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "violations": [
                {"severity": v.severity, "type": v.type, "description": v.description, "items": list(v.items)}
                for v in self.violations
            ],
        }


def parse_erc_report(text: str) -> ErcReport:
    data = json.loads(text)
    violations = []
    for sheet in data.get("sheets", []):
        for v in sheet.get("violations", []):
            violations.append(
                ErcViolation(
                    severity=v.get("severity", ""),
                    type=v.get("type", ""),
                    description=v.get("description", ""),
                    items=tuple(i.get("description", "") for i in v.get("items", [])),
                )
            )
    return ErcReport(kicad_version=data.get("kicad_version", ""), violations=tuple(violations), raw=data)
