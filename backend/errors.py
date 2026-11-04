"""API error contract (plan section 8): error_code, message, affected_component_ids, recovery_hint."""

from __future__ import annotations

from typing import Any

from circuit_core import Issue


class ApiError(Exception):
    def __init__(self, status: int, error_code: str, message: str, *,
                 affected_component_ids: tuple[str, ...] | list[str] = (),
                 recovery_hint: str | None = None, details: Any = None):
        super().__init__(message)
        self.status = status
        self.error_code = error_code
        self.message = message
        self.affected_component_ids = list(affected_component_ids)
        self.recovery_hint = recovery_hint
        self.details = details

    def to_dict(self) -> dict:
        body = {
            "error_code": self.error_code,
            "message": self.message,
            "affected_component_ids": self.affected_component_ids,
            "recovery_hint": self.recovery_hint,
        }
        if self.details is not None:
            body["details"] = self.details
        return body


def issue_dict(issue: Issue) -> dict:
    return {
        "error_code": issue.error_code,
        "severity": issue.severity.value,
        "message": issue.message,
        "affected_component_ids": list(issue.affected_component_ids),
        "recovery_hint": issue.recovery_hint,
    }


def not_found(kind: str, ident: object) -> ApiError:
    return ApiError(404, f"{kind.upper()}_NOT_FOUND", f"{kind} '{ident}' was not found.")
