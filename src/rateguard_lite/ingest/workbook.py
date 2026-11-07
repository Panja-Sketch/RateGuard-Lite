"""openpyxl loading: sheet inventory + grids with formatting flags (spec 5.2)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.utils import get_column_letter

from ..config import LimitsCfg
from ..errors import UnsafeInputError


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class SheetInfo:
    name: str
    visibility: str
    tab_color: str | None
    used_range: str
    merged: list[str]
    row_count: int
    col_count: int


@dataclass
class Grid:
    """Raw values of one sheet (1-based coordinates are row index + 1)."""

    name: str
    rows: list[tuple[Any, ...]]
    flags: dict[tuple[int, int], set[str]] = field(default_factory=dict)      # (row0, col0) -> flags
    numfmt: dict[tuple[int, int], str] = field(default_factory=dict)
    hidden_rows: set[int] = field(default_factory=set)                        # row0
    hidden_cols: set[int] = field(default_factory=set)                        # col0
    visibility: str = "visible"

    def cell(self, r: int, c: int) -> Any:
        if 0 <= r < len(self.rows) and 0 <= c < len(self.rows[r]):
            return self.rows[r][c]
        return None

    @property
    def width(self) -> int:
        return max((len(r) for r in self.rows), default=0)

    def used_range(self) -> str:
        if not self.rows:
            return "A1:A1"
        return f"A1:{get_column_letter(max(self.width, 1))}{len(self.rows)}"


_LETTERS = [get_column_letter(i) for i in range(1, 200)]


def coord(r0: int, c0: int) -> str:
    return f"{_LETTERS[c0] if c0 < 199 else get_column_letter(c0 + 1)}{r0 + 1}"


class WorkbookReader:
    """Open a workbook once; produce inventory and grids. Never evaluates formulas or macros."""

    def __init__(self, path: Path, limits: LimitsCfg, formatting: bool = True) -> None:
        self.path = Path(path)
        self.limits = limits
        self.formatting = formatting
        self.sha256 = sha256_file(self.path)
        try:
            # read_only for exports: far less memory; formatting only needed for approved workbooks
            self._wb_vals = openpyxl.load_workbook(
                self.path, data_only=True, read_only=not formatting, keep_vba=False, keep_links=False
            )
            self._wb_forms = (
                openpyxl.load_workbook(self.path, data_only=False, read_only=False, keep_vba=False, keep_links=False)
                if formatting
                else None
            )
        except Exception as exc:  # noqa: BLE001 - corrupt files raise assorted types
            raise UnsafeInputError(
                f"We couldn't open {self.path.name}. It may be damaged or in an unexpected format.",
                where=self.path.name,
                next_step="Open it in Excel, save a fresh copy as .xlsx, and run again.",
            ) from exc
        self._cells_read = 0

    def close(self) -> None:
        self._wb_vals.close()
        if self._wb_forms is not None:
            self._wb_forms.close()

    def __enter__(self) -> WorkbookReader:
        return self

    def __exit__(self, *a: object) -> None:
        self.close()

    # ------------------------------------------------------------ inventory
    def inventory(self) -> list[SheetInfo]:
        out: list[SheetInfo] = []
        for ws in self._wb_vals.worksheets:
            mc = getattr(ws, "merged_cells", None)
            merged = [str(m) for m in mc.ranges] if mc is not None else []
            color = None
            try:
                tc = ws.sheet_properties.tabColor
                color = tc.rgb if tc is not None and isinstance(tc.rgb, str) else None
            except Exception:  # noqa: BLE001
                color = None
            try:
                rows, cols = ws.max_row or 0, ws.max_column or 0
            except Exception:  # noqa: BLE001
                rows, cols = 0, 0
            out.append(
                SheetInfo(
                    name=ws.title,
                    visibility=getattr(ws, "sheet_state", "visible"),
                    tab_color=color,
                    used_range=f"A1:{get_column_letter(max(cols, 1))}{max(rows, 1)}",
                    merged=merged,
                    row_count=rows,
                    col_count=cols,
                )
            )
        return out

    def sheet_names(self) -> list[str]:
        return list(self._wb_vals.sheetnames)

    # ------------------------------------------------------------ grids
    def grid(self, name: str) -> Grid:
        ws = self._wb_vals[name]
        vis = getattr(ws, "sheet_state", "visible")
        rows: list[tuple[Any, ...]] = []
        for row in ws.iter_rows(values_only=True):
            rows.append(tuple(row))
            self._cells_read += len(row)
            if self._cells_read > self.limits.max_cells:
                raise UnsafeInputError(
                    f"{self.path.name} is too big: it has more than {self.limits.max_cells:,} cells.",
                    where=f"{self.path.name}!{name}",
                    next_step="Split the workbook, or ask your admin to raise limits.max_cells (the most allowed is 10,000,000).",
                )
        # trim trailing fully-empty rows
        while rows and all(v is None or (isinstance(v, str) and not v.strip()) for v in rows[-1]):
            rows.pop()
        g = Grid(name=name, rows=rows, visibility=vis)
        if self.formatting and self._wb_forms is not None:
            self._add_formatting(g, ws, self._wb_forms[name])
        return g

    def _add_formatting(self, g: Grid, ws_vals: Any, ws_f: Any) -> None:
        # hidden rows / columns
        for idx, dim in ws_f.row_dimensions.items():
            if dim.hidden:
                g.hidden_rows.add(idx - 1)
        for dim in ws_f.column_dimensions.values():
            if dim.hidden:
                lo, hi = (dim.min or 0), (dim.max or 0)
                for c in range(lo, hi + 1):
                    g.hidden_cols.add(c - 1)
        # merged ranges: fill down/across from the anchor cell
        for rng in ws_f.merged_cells.ranges:
            anchor = g.cell(rng.min_row - 1, rng.min_col - 1)
            for r in range(rng.min_row, rng.max_row + 1):
                for c in range(rng.min_col, rng.max_col + 1):
                    g.flags.setdefault((r - 1, c - 1), set()).add("MERGED")
                    if (r, c) != (rng.min_row, rng.min_col) and r - 1 < len(g.rows):
                        row = list(g.rows[r - 1])
                        while len(row) <= c - 1:
                            row.append(None)
                        row[c - 1] = anchor
                        g.rows[r - 1] = tuple(row)
        max_r = len(g.rows)
        for row in ws_f.iter_rows(min_row=1, max_row=max_r):
            for cell in row:
                r0, c0 = cell.row - 1, cell.column - 1
                fl: set[str] | None = None
                if cell.data_type == "f":
                    fl = g.flags.setdefault((r0, c0), set())
                    if g.cell(r0, c0) is None:
                        fl.add("FORMULA_NO_VALUE")
                    else:
                        fl.add("FORMULA_CACHED")
                if cell.value is None and cell.data_type != "f":
                    continue
                font = cell.font
                if font is not None and font.strike:
                    (fl if fl is not None else g.flags.setdefault((r0, c0), set())).add("STRIKETHROUGH")
                fill = cell.fill
                if fill is not None and fill.fill_type == "solid":
                    rgb = fill.fgColor.rgb if isinstance(fill.fgColor.rgb, str) else None
                    if rgb and rgb not in ("00000000", "FFFFFFFF"):
                        g.flags.setdefault((r0, c0), set()).add(f"HIGHLIGHTED:{rgb}")
                if cell.number_format and cell.number_format != "General":
                    g.numfmt[(r0, c0)] = cell.number_format
