"""I3/I4: Guidewire 'Export to Spreadsheet' ingestion (spec 5.3)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Literal

from ..config import Config
from ..guard.safety import safety_gate_workbook
from ..learning.context import LearnedContext
from ..mapping.identity import canonical_id
from ..model.core import ColumnType, RateBook, RateTable
from .approved import META_SHEETS
from .fastxlsx import FastWorkbookReader
from .layout import GwLayout, detect_gw_layout, parse_type_token
from .result import IngestResult, SheetIssue
from .tablebuild import build_table, map_type

BOOK_KEYS = ("rate_book_code", "edition", "effective_date", "expiration_date", "jurisdiction", "lob", "status")


def ingest_export(
    path: Path,
    cfg: Config,
    origin: Literal["EXPORT", "BASELINE"] = "EXPORT",
    lob: str | None = None,
    learned: LearnedContext | None = None,
    only_sheets: set[str] | None = None,
) -> IngestResult:
    path = Path(path)
    rep = safety_gate_workbook(path, cfg.limits)
    res = IngestResult(book=RateBook(origin=origin, tables={}, metadata={}, unmapped_sheets=()))
    res.warnings.extend(rep.warnings)
    res.info.extend(rep.info)
    tables: dict[str, RateTable] = {}
    unmapped: list[str] = []
    meta_votes: dict[str, Counter[str]] = {k: Counter() for k in BOOK_KEYS}
    with FastWorkbookReader(path, cfg.limits) as rd:
        res.input_files.append({"name": path.name, "sha256": rd.sha256})
        inv = rd.inventory()
        res.inventory[path.name] = inv
        for s in inv:
            if only_sheets is not None and s.name not in only_sheets:
                continue
            if s.visibility != "visible":
                res.issues.append(SheetIssue(path.name, s.name, "HIDDEN", f"Sheet is {s.visibility}; not used."))
                unmapped.append(s.name)
                continue
            g = rd.grid(s.name)
            lay = detect_gw_layout(g, cfg.limits.max_header_scan_rows, cfg.dates.order)
            if not isinstance(lay, GwLayout):
                if s.name.strip().upper() in META_SHEETS:
                    unmapped.append(s.name)
                else:
                    res.issues.append(SheetIssue(path.name, s.name, "NEEDS_MAPPING", lay.reason))
                    unmapped.append(s.name)
                continue
            md = dict(lay.metadata)
            for k in BOOK_KEYS:
                if k in md:
                    meta_votes[k][md[k]] += 1
            declared: dict[int, tuple[ColumnType, int | None]] = {}
            for i, v in enumerate(g.rows[lay.type_row]):
                tok = parse_type_token(v)
                if tok is not None:
                    declared[i] = (map_type(tok[0]), tok[1])
            code = md.get("table_code", "")
            tname = md.get("table_name", "")
            eff_lob = lob or md.get("lob") or "GW"
            nid = canonical_id(eff_lob, code, tname, s.name)
            if nid in tables:
                res.issues.append(SheetIssue(path.name, s.name, "NEEDS_MAPPING", f"Table identity {nid} appears on more than one sheet."))
                unmapped.append(s.name)
                continue
            t = build_table(
                grid=g, file_name=path.name, sha256=rd.sha256, origin=origin, native_id=nid,
                display_name=tname or code or s.name, header_row=lay.header_row, data_start=lay.data_start,
                data_end=len(g.rows), declared_types=declared,
                metadata={**md, "sheet_name": s.name}, cfg=cfg, learned=learned,
                extra_warnings=lay.notes,
            )
            tables[nid] = t
    book_meta = {k: c.most_common(1)[0][0] for k, c in meta_votes.items() if c}
    res.book = RateBook(origin=origin, tables=tables, metadata=book_meta, unmapped_sheets=tuple(unmapped))
    return res
