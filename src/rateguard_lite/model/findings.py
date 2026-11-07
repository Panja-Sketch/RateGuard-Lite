"""Findings, verdicts and the run result (spec 11.7, 12, 13)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Severity(StrEnum):
    BLOCKER = "BLOCKER"
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    WARNING = "WARNING"
    INFO = "INFO"


class Status(StrEnum):
    NEEDS_MAPPING = "NEEDS_MAPPING"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NOT_EVALUATED = "NOT_EVALUATED"


class Verdict(StrEnum):
    PASS = "PASS"
    PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
    PASS_WITH_LIMITATIONS = "PASS_WITH_LIMITATIONS"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"


GATE_TEXT = {
    Verdict.PASS: "Ready for UAT",
    Verdict.PASS_WITH_WARNINGS: "Values verified; review the warnings",
    Verdict.PASS_WITH_LIMITATIONS: "Values verified; scope checks not performed (no baseline)",
    Verdict.NEEDS_REVIEW: "Human review needed before promotion",
    Verdict.FAIL: "Do not promote: fix listed items",
    Verdict.BLOCKED: "Tool could not verify: resolve mappings/input issues",
}


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    severity: Severity | None = None
    status: Status | None = None
    table_id: str = ""
    table_name: str = ""
    key: str = ""
    column: str = ""
    approved_value: str = ""
    export_value: str = ""
    approved_ref: str = ""   # "file!sheet!cell"
    export_ref: str = ""
    message: str = ""
    explanation: str = ""
    suggested_fix: str = ""
    details: dict[str, Any] = Field(default_factory=dict)

    @property
    def level(self) -> str:
        return (self.severity or self.status or "INFO").value  # type: ignore[union-attr]

    def sort_key(self) -> tuple[int, str, str, str, str]:
        order = {"BLOCKER": 0, "CRITICAL": 1, "HIGH": 2, "NEEDS_MAPPING": 3, "NEEDS_REVIEW": 4,
                 "NOT_EVALUATED": 5, "WARNING": 6, "INFO": 7}
        return (order.get(self.level, 9), self.table_id, self.rule_id, self.key, self.column)
