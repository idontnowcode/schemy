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
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

SCHEMA_VERSION = "0.9.0"
"""0.2.0 added optional ``sheets`` (pages); 0.3.0 added optional ``Component.manufacturer`` and
``Pin.no_connect``; 0.4.0 added optional ``Component.origin``; 0.5.0 added optional ``notes``; 0.6.0 added
optional ``Placement.mirror``; 0.7.0 added optional ``decisions``; 0.8.0 added optional ``Net.voltage`` and
``Net.load_current``; 0.9.0 added optional ``Net.voltage_min``/``voltage_max``, ``Component.specs`` and
``Decision.waives``. Older documents are still valid."""

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
    no_connect: bool = Field(
        default=False,
        description="Intentionally left unconnected; the schematic gets a no-connect marker on this pin.",
    )


class Evidence(_Model):
    """Where a pin map or rating came from."""

    status: VerificationStatus = VerificationStatus.UNVERIFIED
    source: str | None = Field(
        default=None, description="Datasheet, library, or review reference."
    )
    notes: str | None = None


Dielectric = Literal["C0G", "X7R", "X5R", "X7S", "Y5V", "ceramic", "tantalum", "polymer", "electrolytic", "film"]


class PartSpecs(_Model):
    """What the exact part is rated for, from its own datasheet: the values the technical review and the datasheet
    checks need that a value string like '10uF' does not say. All optional; unset means unknown (not checked)."""

    voltage_rating: float | None = Field(default=None, gt=0, le=100_000, description="Rated voltage (V): a "
                                         "capacitor's rated voltage, a diode's reverse voltage, a fuse's maximum "
                                         "voltage.")
    current_rating: float | None = Field(default=None, gt=0, le=10_000, description="Rated current (A): a fuse's "
                                         "hold current, a diode's average forward current, an inductor's saturation "
                                         "current.")
    power_rating: float | None = Field(default=None, gt=0, le=10_000, description="Rated power (W), e.g. a resistor.")
    tolerance: float | None = Field(default=None, gt=0, le=100, description="Tolerance in percent (1 = ±1%).")
    dielectric: Dielectric | None = Field(default=None, description="Capacitor type: ceramic class (C0G, X7R, X5R …), "
                                          "tantalum, polymer, electrolytic or film.")
    esr: float | None = Field(default=None, gt=0, le=1000, description="Equivalent series resistance (Ω) from the "
                              "capacitor's datasheet, for regulators that need an ESR range for stability.")
    forward_voltage: float | None = Field(default=None, gt=0, le=100, description="Forward voltage (V) at the design "
                                          "current, e.g. an LED (2.0 V yellow-green, 3.0 V InGaN green).")


class Component(_Model):
    id: StableId
    reference: Reference
    value: str = Field(min_length=1)
    symbol_id: LibId = Field(description="KiCad symbol, e.g. 'Device:R'.")
    footprint_id: str | None = Field(
        default=None, description="KiCad footprint, e.g. 'Resistor_SMD:R_0603_1608Metric'."
    )
    manufacturer: str | None = Field(
        default=None, description="Maker of the exact part, e.g. 'Texas Instruments'. With the MPN it "
        "identifies the part for datasheet lookups.",
    )
    manufacturer_part_number: str | None = None
    pins: tuple[Pin, ...] = Field(min_length=1)
    pin_map_evidence: Evidence = Field(default_factory=Evidence)
    description: str | None = None
    origin: str | None = Field(
        default=None, max_length=120,
        description="Where the part came from: 'template:<id>@<version>' for a part placed from a reviewed "
        "template, 'ai' for a part an AI design chose, None for parts entered by hand or imported.",
    )
    specs: PartSpecs | None = Field(default=None, description="Ratings of the exact part (from its datasheet).")

    def pin_numbers(self) -> tuple[str, ...]:
        return tuple(p.number for p in self.pins)


class NetNode(_Model):
    component_id: StableId
    pin: PinNumber


class Net(_Model):
    id: StableId
    name: NetName
    nodes: tuple[NetNode, ...] = Field(min_length=1)
    voltage: float | None = Field(
        default=None, ge=-1000, le=1000,
        description="Nominal voltage (V) for the technical review, when the name does not say it (PWR_IN) or not "
        "exactly (+24V after a series diode). Unset: read from the name (+5V, +3V3). Not used for drawing.",
    )
    load_current: float | None = Field(
        default=None, ge=0, le=100,
        description="Current (A) the loads draw from this net, for the technical review (regulator heat and "
        "current, series diode current). Unset: the review assumes 100 mA where it needs a current.",
    )
    voltage_min: float | None = Field(
        default=None, ge=-1000, le=1000,
        description="Lowest voltage the net can have (V), e.g. 4.75 for a 5 V supply at -5%. The review checks "
                    "dropout and minimum ratings at this value. Unset: the nominal voltage.",
    )
    voltage_max: float | None = Field(
        default=None, ge=-1000, le=1000,
        description="Highest voltage the net can have (V), e.g. 5.25 for 5 V +5%. The review checks voltage "
                    "ratings and heat at this value. Unset: the nominal voltage.",
    )

    @model_validator(mode="after")
    def _range_holds(self) -> Net:
        lo, hi, nom = self.voltage_min, self.voltage_max, self.voltage
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(f"net {self.name}: voltage_min {lo} is above voltage_max {hi}")
        if nom is not None and ((lo is not None and nom < lo) or (hi is not None and nom > hi)):
            raise ValueError(f"net {self.name}: voltage {nom} is outside {lo}..{hi}")
        return self


class Placement(_Model):
    """Schematic position of a symbol origin, in millimetres (KiCad sheet coords)."""

    x: float
    y: float
    rotation: int = Field(default=0, description="Counter-clockwise degrees: 0/90/180/270.")
    mirror: bool = Field(default=False, description="Mirrored left-right on the sheet after the rotation (KiCad "
                         "'mirror y'). KiCad's 'mirror x' is the same as this plus a 180 degree turn.")


class Sheet(_Model):
    """One page of a board's schematic. Pages only group components for drawing;
    nets stay board-wide, so moving a component to another page never changes connectivity."""

    id: StableId
    name: Annotated[str, StringConstraints(min_length=1, max_length=60, pattern=r"^[^/\\:*?\"<>|]+$")]
    components: tuple[StableId, ...] = ()


NoteText = Annotated[str, StringConstraints(min_length=1, max_length=120, pattern=r"^[^\r\n]+$")]


class Note(_Model):
    """A short line of text drawn on the schematic: what a block is and its key spec ("555 astable,
    ~1 Hz"), or what a net carries ("555 output, 1 Hz square wave"). Notes never change connectivity.

    With ``components`` the note goes next to those parts; with ``net`` next to that net's label; with
    neither, at the top of the first page. Write notes in English so they read the same in every tool."""

    id: StableId
    text: NoteText
    components: tuple[StableId, ...] = ()
    net: NetName | None = None


DecisionBy = Literal["requirement", "changed", "ai", "template", "engineer"]
"""Who made a design choice:

- ``requirement``: the user asked for it and it was taken as asked;
- ``changed``: the user asked for something else, and it was changed (``request`` says what was asked,
  ``reason`` why it was changed);
- ``ai``: chosen by an AI (or the design assistant) where the requirements said nothing;
- ``template``: fixed by a reviewed template;
- ``engineer``: decided by a person reviewing the design."""


class Decision(_Model):
    """One design choice and why it was made, kept with the circuit so a reviewer can follow every choice: a part,
    a value, an added protection, a topology. Decisions never change connectivity."""

    id: StableId
    topic: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    choice: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    by: DecisionBy
    reason: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    request: str | None = Field(default=None, max_length=1000, description="The user's words this answers.")
    calc: str | None = Field(default=None, max_length=1000, description="The calculation behind a value.")
    source: str | None = Field(default=None, max_length=500,
                               description="Datasheet (document, section, page), standard or reference used.")
    components: tuple[StableId, ...] = ()
    waives: tuple[Annotated[str, StringConstraints(pattern=r"^G-[A-Z0-9-]{1,20}$")], ...] = Field(
        default=(), description="Design-guide entries (ids) this decision deliberately does not follow; the reason "
                                "is the decision's reason. The guide checklist shows them as exceptions.")


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
    notes: tuple[Note, ...] = Field(
        default=(), description="Short English notes drawn on the schematic (block captions, net descriptions).",
    )
    decisions: tuple[Decision, ...] = Field(
        default=(), description="Design choices and their reasons (who decided, calculation, source), for review.",
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
