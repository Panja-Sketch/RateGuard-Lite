"""Canonical Rate Table Model (spec section 3). The only thing ever compared.

Implemented as frozen, slotted dataclasses rather than pydantic models: these objects are built
only by our own ingestion code (never from untrusted structured input), and a 600k-cell export
makes pydantic validation the dominant cost. See docs/DECISIONS.md D-002.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal


@dataclass(frozen=True, slots=True)
class SourceRef:
    file: str            # file name only, never an absolute path
    sheet: str
    cell_range: str
    sha256: str


class ColumnRole(StrEnum):
    KEY = "KEY"
    RANGE_MIN = "RANGE_MIN"
    RANGE_MAX = "RANGE_MAX"
    FACTOR = "FACTOR"
    IGNORE = "IGNORE"


class ColumnType(StrEnum):
    STRING = "STRING"
    INTEGER = "INTEGER"
    DECIMAL = "DECIMAL"
    DATE = "DATE"
    BOOLEAN = "BOOLEAN"


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    canonical_name: str
    source_header: str
    role: ColumnRole
    type: ColumnType
    scale: int | None = None
    wildcard_tokens: frozenset[str] = frozenset()
    range_convention: Literal["INCLUSIVE_INCLUSIVE", "INCLUSIVE_EXCLUSIVE"] | None = None


@dataclass(frozen=True, slots=True)
class CellValue:
    kind: Literal["VALUE", "WILDCARD", "BLANK"]
    value: str | None            # canonical string form
    raw: str | None              # original text, for evidence
    coord: str                   # e.g. "C14"
    flags: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Row:
    key: tuple[CellValue, ...]
    factors: dict[str, CellValue]
    source_row: int


@dataclass(frozen=True, slots=True)
class RateTable:
    canonical_id: str
    display_name: str
    origin: Literal["APPROVED", "EXPORT", "BASELINE"]
    source: SourceRef
    columns: tuple[ColumnSpec, ...]
    rows: tuple[Row, ...]
    metadata: dict[str, str]
    completeness: Literal["FULL", "PARTIAL", "UNKNOWN"]
    warnings: tuple[str, ...] = ()

    def key_columns(self) -> tuple[ColumnSpec, ...]:
        return tuple(c for c in self.columns if c.role in (ColumnRole.KEY, ColumnRole.RANGE_MIN, ColumnRole.RANGE_MAX))

    def factor_columns(self) -> tuple[ColumnSpec, ...]:
        return tuple(c for c in self.columns if c.role == ColumnRole.FACTOR)


@dataclass(frozen=True, slots=True)
class RateBook:
    origin: Literal["APPROVED", "EXPORT", "BASELINE"]
    tables: dict[str, RateTable]
    metadata: dict[str, str]
    unmapped_sheets: tuple[str, ...] = field(default=())
