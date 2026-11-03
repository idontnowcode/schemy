"""Circuit IR models and rule checks (no EDA or AI dependencies)."""

from .models import (
    SCHEMA_VERSION,
    CircuitIR,
    Component,
    Evidence,
    Net,
    NetNode,
    Pin,
    Placement,
    Validation,
    VerificationStatus,
)
from .validation import Issue, Severity, ValidationResult, validate_circuit

__all__ = [
    "SCHEMA_VERSION",
    "CircuitIR",
    "Component",
    "Evidence",
    "Issue",
    "Net",
    "NetNode",
    "Pin",
    "Placement",
    "Severity",
    "Validation",
    "ValidationResult",
    "VerificationStatus",
    "validate_circuit",
]
