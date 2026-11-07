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


# D-027: plain-language headlines (the first four are the wording agreed with QA)
GATE_TEXT = {
    Verdict.PASS: "All approved values match PolicyCenter. Ready for QA testing.",
    Verdict.PASS_WITH_WARNINGS: "Values match, with a few things worth a quick look.",
    Verdict.PASS_WITH_LIMITATIONS: "Values match, but some checks could not be done. See the notes below.",
    Verdict.NEEDS_REVIEW: "Checking is done, but a few items need a person to look at them.",
    Verdict.FAIL: "Some values in PolicyCenter don't match what was approved. Fix these before QA.",
    Verdict.BLOCKED: "The tool couldn't finish checking. Some tables or files need your help first.",
}

# Plain names for the severity/status codes shown to people (the codes stay in JSON).
LEVEL_LABEL = {
    "BLOCKER": "Blocker", "CRITICAL": "Critical", "HIGH": "High", "NEEDS_MAPPING": "Needs your help",
    "NEEDS_REVIEW": "Needs a look", "NOT_EVALUATED": "Not checked", "WARNING": "Warning", "INFO": "Note",
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
    plain_what: str = ""       # D-027: what we found
    plain_why: str = ""        # D-027: why it matters
    plain_action: str = ""     # D-027: what to do
    details: dict[str, Any] = Field(default_factory=dict)

    @property
    def level_label(self) -> str:
        return LEVEL_LABEL.get(self.level, self.level.title())

    @property
    def level(self) -> str:
        return (self.severity or self.status or "INFO").value  # type: ignore[union-attr]

    def sort_key(self) -> tuple[int, str, str, str, str]:
        order = {"BLOCKER": 0, "CRITICAL": 1, "HIGH": 2, "NEEDS_MAPPING": 3, "NEEDS_REVIEW": 4,
                 "NOT_EVALUATED": 5, "WARNING": 6, "INFO": 7}
        return (order.get(self.level, 9), self.table_id, self.rule_id, self.key, self.column)
