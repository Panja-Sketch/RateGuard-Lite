"""Machine-readable report model; its JSON schema is written to schemas/report.schema.json."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .findings import Finding


class InputRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str
    name: str
    sha256: str


class Report(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "1.0"
    tool: dict[str, str]
    run: dict[str, str]
    inputs: list[InputRef]
    verdict: str
    gate: str
    exit_code: int
    summary: dict[str, Any]
    jira: dict[str, Any]
    baseline: dict[str, Any]
    findings: list[Finding]
    scope_matrix: list[dict[str, Any]]
    mapping: list[dict[str, Any]]
    warnings: list[str] = Field(default_factory=list)
    info: list[str] = Field(default_factory=list)
    learned_used: list[dict[str, Any]] = Field(default_factory=list)
    tolerance_overrides: list[str] = Field(default_factory=list)
    test_scenarios: dict[str, int] = Field(default_factory=dict)
