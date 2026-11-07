"""Build canonical RateTables from a grid + detected layout (spec 3, 5.4, 5.5)."""

from __future__ import annotations

from typing import Any, Literal

from ..config import Config, aux
from ..learning.context import LearnedContext
from ..model.core import CellValue, ColumnRole, ColumnSpec, ColumnType, RateTable, Row, SourceRef
from .normalize import canonical_colname, infer_type, normalize_cell, wildcard_tokens
from .workbook import Grid, coord

_FACTOR_TYPES = (ColumnType.DECIMAL, ColumnType.INTEGER, ColumnType.BOOLEAN)
_NUMERIC = (ColumnType.DECIMAL, ColumnType.INTEGER)
_TYPE_MAP = {
    "DECIMAL": ColumnType.DECIMAL, "BIGDECIMAL": ColumnType.DECIMAL, "DOUBLE": ColumnType.DECIMAL,
    "NUMBER": ColumnType.DECIMAL, "INTEGER": ColumnType.INTEGER, "INT": ColumnType.INTEGER,
    "LONG": ColumnType.INTEGER, "STRING": ColumnType.STRING, "TEXT": ColumnType.STRING,
    "DATE": ColumnType.DATE, "DATETIME": ColumnType.DATE, "BOOLEAN": ColumnType.BOOLEAN, "BOOL": ColumnType.BOOLEAN,
}


def map_type(token: str) -> ColumnType:
    return _TYPE_MAP.get(token.upper(), ColumnType.STRING)


def _empty(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _range_pairs(names: list[str]) -> dict[str, ColumnRole]:
    out: dict[str, ColumnRole] = {}
    pairs = aux("synonyms").get("range_suffix_pairs", [])
    for lo, hi in pairs:
        for n in names:
            if n.endswith(lo) and len(n) > len(lo):
                base = n[: -len(lo)].rstrip("_")
                hi_name = next((m for m in names if m.endswith(hi) and m[: -len(hi)].rstrip("_") == base), None)
                if hi_name and hi_name != n:
                    out[n] = ColumnRole.RANGE_MIN
                    out[hi_name] = ColumnRole.RANGE_MAX
    return out


def infer_columns(
    headers: list[str],
    types: list[ColumnType],
    scales: list[int | None],
    table_native_id: str,
    learned: LearnedContext | None,
) -> list[ColumnSpec]:
    syn = learned.column_synonyms if learned else None
    names: list[str] = []
    for h in headers:
        n = canonical_colname(h, syn) or "col"
        base, k = n, 2
        while n in names:
            n, k = f"{base}_{k}", k + 1
        names.append(n)
    markers = [m.lower() for m in aux("gw_layouts")["profiles"][0]["factor_markers"]]
    desc = {d.lower() for d in aux("synonyms").get("description_columns", [])}
    ranges = _range_pairs(names)
    learned_roles = (learned.column_roles.get(table_native_id, {}) if learned else {})
    roles: list[ColumnRole] = []
    for n, t in zip(names, types, strict=True):
        if n in learned_roles:
            roles.append(ColumnRole(learned_roles[n]))
        elif n in desc or any(n.endswith("_" + d) for d in desc if d in ("description", "notes", "comment", "comments")):
            roles.append(ColumnRole.IGNORE)
        elif n in ranges and t in _NUMERIC:
            roles.append(ranges[n])
        elif t in _FACTOR_TYPES and any(m in n for m in markers):
            roles.append(ColumnRole.FACTOR)
        else:
            roles.append(ColumnRole.KEY)
    if ColumnRole.FACTOR not in roles:
        for i in range(len(names) - 1, -1, -1):
            if roles[i] == ColumnRole.KEY and types[i] in _NUMERIC and names[i] not in learned_roles:
                roles[i] = ColumnRole.FACTOR
                break
    specs = []
    for h, n, t, s, r in zip(headers, names, types, scales, roles, strict=True):
        specs.append(ColumnSpec(canonical_name=n, source_header=h, role=r, type=t, scale=s if r == ColumnRole.FACTOR else None))
    return specs


def _cell(ctx: tuple[Grid, int, tuple[Any, ...], frozenset[str], bool, Any], c: int, spec: ColumnSpec, tokens: frozenset[str]) -> CellValue:
    grid, r, vals, row_flags, has_flags, date_order = ctx
    raw = vals[c] if c < len(vals) else None
    fl = row_flags
    nf = None
    if has_flags:
        extra = grid.flags.get((r, c))
        if extra or c in grid.hidden_cols:
            s2 = set(row_flags) | set(extra or ())
            if c in grid.hidden_cols:
                s2.add("HIDDEN_COL")
            if "MERGED" in s2 and spec.role in (ColumnRole.KEY, ColumnRole.RANGE_MIN, ColumnRole.RANGE_MAX):
                s2.add("MERGED_KEY_FILLED")
            fl = frozenset(s2)
        nf = grid.numfmt.get((r, c))
    return normalize_cell(raw, role=spec.role, ctype=spec.type, coord=coord(r, c), tokens=tokens,
                          number_format=nf, flags=fl, date_order=date_order)


def build_table(
    *,
    grid: Grid,
    file_name: str,
    sha256: str,
    origin: Literal["APPROVED", "EXPORT", "BASELINE"],
    native_id: str,
    display_name: str,
    header_row: int,
    data_start: int,
    data_end: int,
    declared_types: dict[int, tuple[ColumnType, int | None]] | None,
    metadata: dict[str, str],
    cfg: Config,
    learned: LearnedContext | None,
    strike_means: str = "review",
    extra_warnings: list[str] | None = None,
) -> RateTable:
    hdr = grid.rows[header_row]
    col_idx = [i for i, v in enumerate(hdr) if not _empty(v)]
    # drop header columns that are completely empty in the data region (stray header cells)
    headers = [str(hdr[i]).strip() for i in col_idx]
    types: list[ColumnType] = []
    scales: list[int | None] = []
    for i in col_idx:
        if declared_types and i in declared_types:
            types.append(declared_types[i][0])
            scales.append(declared_types[i][1])
        else:
            col_vals = [grid.rows[r][i] if i < len(grid.rows[r]) else None for r in range(data_start, data_end)]
            types.append(infer_type(col_vals))
            scales.append(None)
    specs = infer_columns(headers, types, scales, native_id, learned)
    warnings = list(extra_warnings or [])
    ncols = len(col_idx)
    rows: list[Row] = []
    key_specs = [(j, s) for j, s in enumerate(specs) if s.role in (ColumnRole.KEY, ColumnRole.RANGE_MIN, ColumnRole.RANGE_MAX)]
    fac_specs = [(j, s) for j, s in enumerate(specs) if s.role == ColumnRole.FACTOR]
    toks: dict[str, frozenset[str]] = {}
    for _, s in key_specs:
        t = set(wildcard_tokens(native_id, s.canonical_name))
        if learned:
            t |= {x.upper() for x in learned.wildcard_tokens.get(native_id, {}).get(s.canonical_name, [])}
        toks[s.canonical_name] = frozenset(t)
    skipped_notes = 0
    last_row = data_start
    last_data_row = max((i for i in range(data_start, min(data_end, len(grid.rows))) if any(not _empty(v) for v in grid.rows[i])), default=-1)
    no_flags: frozenset[str] = frozenset()
    date_order = cfg.dates.order
    key_plan = [(col_idx[j], sp, toks[sp.canonical_name]) for j, sp in key_specs]
    fac_plan = [(col_idx[j], sp) for j, sp in fac_specs]
    has_flags = bool(grid.flags or grid.hidden_rows or grid.hidden_cols or grid.numfmt)
    for r in range(data_start, data_end):
        vals: tuple[Any, ...] = grid.rows[r] if r < len(grid.rows) else ()
        nvals = len(vals)
        nonempty = [i for i in col_idx if i < nvals and not _empty(vals[i])]
        if not nonempty:
            # Approved workbooks have gaps. In a Guidewire export an interior blank row is data:
            # wildcard keys with a blank factor (the classic "blank loaded instead of wildcard" defect).
            if origin == "APPROVED" or r >= last_data_row:
                continue
        if len(nonempty) == 1 and ncols >= 3 and origin == "APPROVED":
            skipped_notes += 1
            continue
        last_row = r
        row_flags: frozenset[str] = no_flags
        if has_flags:
            rf: set[str] = set()
            if r in grid.hidden_rows:
                rf.add("HIDDEN_ROW")
            struck = sum(1 for i in nonempty if "STRIKETHROUGH" in grid.flags.get((r, i), ()))
            if struck and struck / len(nonempty) >= 0.5:
                rf.add("STRIKETHROUGH")
            row_flags = frozenset(rf)

        ctx = (grid, r, vals, row_flags, has_flags, date_order)
        key = tuple(_cell(ctx, c, sp, tk) for c, sp, tk in key_plan)
        factors = {sp.canonical_name: _cell(ctx, c, sp, no_flags) for c, sp in fac_plan}
        rows.append(Row(key=key, factors=factors, source_row=r + 1))
    if skipped_notes:
        warnings.append(f"Skipped {skipped_notes} note row(s) below the header.")
    max_col = max(col_idx) if col_idx else 0
    return RateTable(
        canonical_id=native_id,
        display_name=display_name,
        origin=origin,
        source=SourceRef(file=file_name, sheet=grid.name, cell_range=f"A{header_row + 1}:{coord(max(last_row, header_row), max_col)}", sha256=sha256),
        columns=tuple(specs),
        rows=tuple(rows),
        metadata=metadata,
        completeness="FULL",
        warnings=tuple(warnings),
    )
