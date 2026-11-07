"""Shared ingestion result types."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..model.core import RateBook
from .workbook import SheetInfo


@dataclass
class SheetIssue:
    file: str
    sheet: str
    status: str       # NEEDS_MAPPING | HIDDEN | SKIPPED
    reason: str


@dataclass
class IngestResult:
    book: RateBook
    issues: list[SheetIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)
    inventory: dict[str, list[SheetInfo]] = field(default_factory=dict)
    input_files: list[dict[str, str]] = field(default_factory=list)   # {name, sha256}
