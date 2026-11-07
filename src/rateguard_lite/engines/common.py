"""Shared helpers for the reconciliation engines."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..model.core import CellValue, RateTable, Row
from ..model.findings import Finding, Severity, Status
from ..plain import apply_plain

KeyT = tuple[tuple[str, str | None], ...]


def make_finding(rule_id: str, *, severity: Severity | None = None, status: Status | None = None, **kw: Any) -> Finding:
    return apply_plain(Finding(rule_id=rule_id, severity=severity, status=status, **kw))


def ref(t: RateTable, cell: CellValue | None) -> str:
    if cell is None:
        return ""
    return f"{t.source.file}!{t.source.sheet}!{cell.coord}"


def key_order(t: RateTable) -> list[int]:
    """Indices into Row.key ordered by canonical column name (stable across column order)."""
    cols = t.key_columns()
    return sorted(range(len(cols)), key=lambda i: cols[i].canonical_name)


def key_names(t: RateTable) -> list[str]:
    cols = t.key_columns()
    return sorted(c.canonical_name for c in cols)


def row_key(row: Row, order: list[int]) -> KeyT:
    return tuple((row.key[i].kind, row.key[i].value) for i in order)


@dataclass
class Index:
    rows: dict[KeyT, Row] = field(default_factory=dict)
    duplicates: list[tuple[KeyT, Row, Row]] = field(default_factory=list)


def build_index(t: RateTable) -> Index:
    order = key_order(t)
    idx = Index()
    for r in t.rows:
        k = row_key(r, order)
        if k in idx.rows:
            idx.duplicates.append((k, idx.rows[k], r))
        else:
            idx.rows[k] = r
    return idx


def key_text(t: RateTable, k: KeyT) -> str:
    cols = sorted(t.key_columns(), key=lambda c: c.canonical_name)
    parts = []
    for c, (kind, val) in zip(cols, k, strict=True):
        shown = "*" if kind == "WILDCARD" else ("(blank)" if val is None else val)
        parts.append(f"{c.source_header}={shown}")
    return ", ".join(parts)


def table_hash(t: RateTable) -> str:
    """Order-independent hash of normalized content; the snapshot fast path (spec 8.7)."""
    order = key_order(t)
    fnames = sorted(c.canonical_name for c in t.factor_columns())
    h = hashlib.sha256()
    h.update(("|".join(key_names(t)) + "#" + "|".join(fnames)).encode())
    lines = []
    for r in t.rows:
        k = row_key(r, order)
        lines.append(
            "\x1f".join(f"{a}:{b}" for a, b in k) + "\x1e" + "\x1f".join(
                f"{r.factors[n].kind}:{r.factors[n].value}" if n in r.factors else "-" for n in fnames)
        )
    lines.sort()
    h.update("\n".join(lines).encode())
    return h.hexdigest()


def to_decimal_safe(s: str | None) -> Decimal | None:
    if s is None:
        return None
    try:
        d = Decimal(s)
    except Exception:  # noqa: BLE001
        return None
    return d if d.is_finite() else None
