"""Header / table-region detection (spec 5.3). Never guesses: ties or low scores -> failure."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..config import aux
from .normalize import AmbiguousDateError, norm_colname, parse_date
from .workbook import Grid

_TYPE_RE = re.compile(r"^([A-Z]+)\s*(?:\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\))?$")


def _empty(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _nonempty_idx(row: tuple[Any, ...]) -> list[int]:
    return [i for i, v in enumerate(row) if not _empty(v)]


@dataclass
class GwLayout:
    type_row: int
    header_row: int
    data_start: int
    metadata: dict[str, str]
    metadata_raw: dict[str, str]
    profile: str
    score: float
    notes: list[str] = field(default_factory=list)


@dataclass
class ApprovedLayout:
    header_row: int
    data_start: int
    data_end: int          # exclusive
    skipped_rows: list[int]
    multiple_tables: bool
    notes: list[str] = field(default_factory=list)


@dataclass
class LayoutFailure:
    reason: str


def parse_type_token(cell: Any) -> tuple[str, int | None] | None:
    """'DECIMAL(10,4)' -> ('DECIMAL', 4); 'STRING' -> ('STRING', None); not a type -> None."""
    if _empty(cell):
        return None
    m = _TYPE_RE.match(str(cell).strip().upper())
    if not m:
        return None
    toks = {t.upper() for t in _profile()["type_tokens"]}
    if m[1] not in toks:
        return None
    # DECIMAL(10,4) -> scale 4; DECIMAL(4) -> scale 4
    scale = int(m[3]) if m[3] is not None else (int(m[2]) if m[2] is not None else None)
    return m[1], scale


def _profile() -> dict[str, Any]:
    profs = aux("gw_layouts")["profiles"]
    first: dict[str, Any] = profs[0]
    return first


def _meta_labels() -> dict[str, str]:
    out: dict[str, str] = {}
    for key, labels in _profile()["metadata_labels"].items():
        for lab in labels:
            out[norm_colname(lab)] = key
    return out


def _is_type_row(row: tuple[Any, ...]) -> bool:
    idx = _nonempty_idx(row)
    if not idx:
        return False
    hits = sum(1 for i in idx if parse_type_token(row[i]) is not None)
    return hits / len(idx) >= 0.6 and hits >= 1


def _has_factor_marker(row: tuple[Any, ...]) -> bool:
    markers = [m.lower() for m in _profile()["factor_markers"]]
    return any(not _empty(v) and any(m in str(v).lower() for m in markers) for v in row)


def extract_metadata(grid: Grid, upto: int, date_order: str = "US_MDY") -> tuple[dict[str, str], dict[str, str], list[str]]:
    labels = _meta_labels()
    norm: dict[str, str] = {}
    raw: dict[str, str] = {}
    notes: list[str] = []
    for r in range(min(upto, len(grid.rows))):
        row = grid.rows[r]
        if len(row) < 2 or _empty(row[0]):
            continue
        key = labels.get(norm_colname(str(row[0]).rstrip(":")))
        if key is None or _empty(row[1]):
            continue
        val = row[1]
        raw[key] = str(val)
        if key in ("effective_date", "expiration_date"):
            try:
                norm[key] = parse_date(val, date_order)[0]
            except AmbiguousDateError:
                norm[key] = str(val).strip()
                notes.append(f"AMBIGUOUS_DATE in metadata '{key}': {val!r}")
        else:
            norm[key] = re.sub(r"\s+", " ", str(val).strip())
    return norm, raw, notes


def detect_gw_layout(grid: Grid, max_scan: int, date_order: str = "US_MDY") -> GwLayout | LayoutFailure:
    prof = _profile()
    labels = _meta_labels()
    scan = min(max_scan, len(grid.rows))
    below = prof.get("header_position", "below_type") == "below_type"
    cands: list[tuple[float, int, int]] = []
    for i in range(scan):
        row = grid.rows[i]
        if not _is_type_row(row):
            continue
        h = i + 1 if below else i - 1
        if h < 0 or h >= len(grid.rows):
            continue
        hdr = grid.rows[h]
        hidx = _nonempty_idx(hdr)
        if not hidx:
            continue
        # a header row must be text, not another type row
        if _is_type_row(hdr):
            continue
        s = 3.0
        if _has_factor_marker(hdr):
            s += 2
        for r in range(0, min(i, h)):
            a = grid.rows[r][0] if grid.rows[r] else None
            if not _empty(a) and norm_colname(str(a).rstrip(":")) in labels:
                s += 1
        cands.append((s, i, h))
    if not cands:
        return LayoutFailure("No data-type row (DECIMAL/INTEGER/STRING...) found in the first "
                             f"{scan} rows. Run `rgl inspect` and confirm the layout with `rgl map`.")
    cands.sort(reverse=True)
    if len(cands) > 1 and cands[0][0] == cands[1][0]:
        return LayoutFailure(f"Two equally plausible header layouts (rows {cands[0][1] + 1} and {cands[1][1] + 1}). "
                             "Confirm the layout with `rgl map`.")
    score, tr, hr = cands[0]
    if score < 3:
        return LayoutFailure("Header layout confidence is too low.")
    ds = max(tr, hr) + 1
    nkeys = len(_nonempty_idx(grid.rows[hr]))
    while ds < len(grid.rows) and not _nonempty_idx(grid.rows[ds]):
        ds += 1
    meta, raw, notes = extract_metadata(grid, min(tr, hr), date_order)
    if nkeys == 0:
        return LayoutFailure("Header row is empty.")
    return GwLayout(type_row=tr, header_row=hr, data_start=ds, metadata=meta, metadata_raw=raw,
                    profile=prof["name"], score=score, notes=notes)


# ------------------------------------------------------------------ approved side
def _is_numeric_like(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str):
        return bool(re.match(r"^[\s$+-]*[\d,]*\.?\d+%?\s*$", v))
    return False


def _looks_header(row: tuple[Any, ...]) -> bool:
    idx = _nonempty_idx(row)
    if len(idx) < 2:
        return False
    textual = sum(1 for i in idx if isinstance(row[i], str) and not _is_numeric_like(row[i]))
    return textual / len(idx) >= 0.5


def detect_approved_layout(grid: Grid, max_scan: int) -> ApprovedLayout | LayoutFailure:
    rows = grid.rows
    scan = min(max_scan, len(rows))
    skipped: list[int] = []
    header = None
    for i in range(scan):
        if _looks_header(rows[i]):
            # following non-blank row must carry data (numeric/code values)
            j = i + 1
            while j < len(rows) and not _nonempty_idx(rows[j]):
                j += 1
            if j >= len(rows):
                continue
            nxt = _nonempty_idx(rows[j])
            if len(nxt) >= max(2, int(0.5 * len(_nonempty_idx(rows[i])))) and any(_is_numeric_like(rows[j][k]) for k in nxt):
                header = i
                break
        if _nonempty_idx(rows[i]):
            skipped.append(i)
    if header is None:
        return LayoutFailure("Could not find a header row. Run `rgl inspect` and confirm the layout with `rgl map`.")
    start = header + 1
    end = len(rows)
    multi = False
    notes: list[str] = []
    blank_run = False
    for r in range(start, len(rows)):
        idx = _nonempty_idx(rows[r])
        if not idx:
            blank_run = True
            continue
        if blank_run and _looks_header(rows[r]) and len(idx) >= 2:
            nrow = r + 1
            if nrow < len(rows) and any(_is_numeric_like(rows[nrow][k]) for k in _nonempty_idx(rows[nrow])):
                multi = True
        blank_run = False
    if multi:
        notes.append("Several tables appear to be stacked on this sheet.")
    return ApprovedLayout(header_row=header, data_start=start, data_end=end, skipped_rows=skipped,
                          multiple_tables=multi, notes=notes)


def failure_reason(grid: Any, reason: str) -> str:
    """D-027: an empty tab gets its own plain reason instead of a header-detection message."""
    if not any(not _empty(v) for row in grid.rows for v in row):
        return "The tab is empty."
    return reason


def is_header_only(grid: Any) -> bool:
    """True when a tab holds a header row and no data rows at all (D-031)."""
    rows = [row for row in grid.rows if _nonempty_idx(row)]
    return bool(rows) and _looks_header(rows[-1]) and not any(_is_numeric_like(v) for row in rows for v in row)
