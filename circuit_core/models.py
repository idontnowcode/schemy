"""Circuit IR - the machine-readable single source of truth for a design.

Design rules (plan section 6):
- Connections are stored by the *physical pin number* of the KiCad symbol.
  Semantic pin names (e.g. ``A``/``K``) are carried only as evidence and are
  cross-checked against the symbol library; they never decide connectivity.
- IDs (component/net) are stable strings chosen once and never re-derived
  from reference designators or positions.
- Schematic placement lives in ``layout``, separate from connectivity, so an
  automatic layout can never change the netlist.
- Anything not checked against a datasheet stays ``unverified``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

SCHEMA_VERSION = "0.2.0"
"""0.2.0 added optional ``sheets`` (pages). 0.1.0 documents are still valid."""

StableId = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]*$", min_length=1, max_length=64),
]
"""Lower-case, stable identifier (e.g. ``res-001``)."""

Reference = Annotated[str, StringConstraints(pattern=r"^[A-Z]+[0-9]+$")]
"""Reference designator such as ``R1`` or ``J12``."""

NetName = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9_+\-]+$", min_length=1, max_length=64)
]
"""Net names become KiCad labels; ``/`` is reserved for sheet paths."""

LibId = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9_.\-]+:[^:\s]+$")
]
"""KiCad library identifier ``<library nickname>:<symbol name>``."""

PinNumber = Annotated[str, StringConstraints(min_length=1, max_length=16)]


class VerificationStatus(StrEnum):
    """Plan FR-11: unverified / needs review / verified (by an engineer)."""

    UNVERIFIED = "unverified"
    NEEDS_REVIEW = "needs_review"
    VERIFIED = "verified"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Pin(_Model):
    """A physical pin of a component, keyed by the symbol's pin number."""

    number: PinNumber
    name: str | None = Field(
        default=None,
        description="Expected pin name in the KiCad symbol (evidence only; "
        "checked against the library, never used for connectivity).",
    )


class Evidence(_Model):
    """Where a pin map or rating came from."""

    status: VerificationStatus = VerificationStatus.UNVERIFIED
    source: str | None = Field(
        default=None, description="Datasheet, library, or review reference."
    )
    notes: str | None = None


class Component(_Model):
    id: StableId
    reference: Reference
    value: str = Field(min_length=1)
    symbol_id: LibId = Field(description="KiCad symbol, e.g. 'Device:R'.")
    footprint_id: str | None = Field(
        default=None, description="KiCad footprint, e.g. 'Resistor_SMD:R_0603_1608Metric'."
    )
    manufacturer_part_number: str | None = None
    pins: tuple[Pin, ...] = Field(min_length=1)
    pin_map_evidence: Evidence = Field(default_factory=Evidence)
    description: str | None = None

    def pin_numbers(self) -> tuple[str, ...]:
        return tuple(p.number for p in self.pins)


class NetNode(_Model):
    component_id: StableId
    pin: PinNumber


class Net(_Model):
    id: StableId
    name: NetName
    nodes: tuple[NetNode, ...] = Field(min_length=1)


class Placement(_Model):
    """Schematic position of a symbol origin, in millimetres (KiCad sheet coords)."""

    x: float
    y: float
    rotation: int = Field(default=0, description="Counter-clockwise degrees: 0/90/180/270.")


class Sheet(_Model):
    """One page of a board's schematic. Pages only group components for drawing;
    nets stay board-wide, so moving a component to another page never changes connectivity."""

    id: StableId
    name: Annotated[str, StringConstraints(min_length=1, max_length=60, pattern=r"^[^/\\:*?\"<>|]+$")]
    components: tuple[StableId, ...] = ()


class Validation(_Model):
    status: VerificationStatus = VerificationStatus.UNVERIFIED
    notes: str | None = None


class CircuitIR(_Model):
    schema_version: str = SCHEMA_VERSION
    project_id: StableId
    title: str | None = None
    components: tuple[Component, ...] = Field(min_length=1)
    nets: tuple[Net, ...] = ()
    sheets: tuple[Sheet, ...] = Field(
        default=(),
        description="Pages in order. Empty means one page. Components not listed go on the first page.",
    )
    layout: dict[StableId, Placement] = Field(
        default_factory=dict,
        description="Optional placement per component id (on its page); kept apart from connectivity.",
    )
    validation: Validation = Field(default_factory=Validation)

    def component(self, component_id: str) -> Component | None:
        for c in self.components:
            if c.id == component_id:
                return c
        return None

    def page_of(self) -> dict[str, str]:
        """component id -> sheet id (the first sheet for unlisted components)."""
        if not self.sheets:
            return {c.id: DEFAULT_SHEET_ID for c in self.components}
        listed = {cid: sheet.id for sheet in self.sheets for cid in sheet.components}
        first = self.sheets[0].id
        return {c.id: listed.get(c.id, first) for c in self.components}

    def effective_sheets(self) -> tuple[Sheet, ...]:
        return self.sheets or (Sheet(id=DEFAULT_SHEET_ID, name="Page 1"),)


DEFAULT_SHEET_ID = "p1"
