"""I1: approved workbook ingestion - single file, folder, or zip (spec 5, 5.1)."""

from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from ..config import Config
from ..errors import UnsafeInputError
from ..guard.safety import safety_gate_workbook, safety_gate_zip
from ..learning.context import LearnedContext
from ..mapping.identity import canonical_id
from ..model.core import RateBook, RateTable
from .layout import ApprovedLayout, detect_approved_layout, extract_metadata, failure_reason
from .result import IngestResult, SheetIssue
from .tablebuild import build_table
from .workbook import WorkbookReader

META_SHEETS = {"META", "INFO", "README", "SUMMARY", "COVER", "NOTES", "INDEX", "CHANGELOG", "CHANGE LOG", "FILING INFO", "TOC"}
_GENERIC = re.compile(r"^(sheet|table|data)\s*\d*$", re.I)


def _workbooks(path: Path, cfg: Config, res: IngestResult) -> tuple[list[Path], Path | None]:
    path = Path(path)
    if path.is_dir():
        files = sorted(p for p in path.iterdir() if p.suffix.lower() in (".xlsx", ".xlsm") and not p.name.startswith("~$"))
        if not files:
            raise UnsafeInputError("The folder contains no .xlsx/.xlsm files.", where=path.name, next_step="Check --approved.")
        return files, None
    if path.suffix.lower() == ".zip":
        rep = safety_gate_zip(path, cfg.limits)
        res.warnings.extend(rep.warnings)
        tmp = Path(tempfile.mkdtemp(prefix="rgl_zip_"))
        files = []
        with zipfile.ZipFile(path) as zf:
            for i, info in enumerate(sorted(zf.infolist(), key=lambda x: x.filename)):
                if info.is_dir() or not info.filename.lower().endswith((".xlsx", ".xlsm")):
                    continue
                dest = tmp / f"{i:03d}_{Path(info.filename.replace(chr(92), '/')).name}"
                with zf.open(info) as src, dest.open("wb") as out:
                    shutil.copyfileobj(src, out)
                files.append(dest)
        if not files:
            raise UnsafeInputError("The zip contains no .xlsx/.xlsm workbooks.", where=path.name)
        return files, tmp
    return [path], None


def ingest_approved(path: Path, cfg: Config, lob: str, learned: LearnedContext | None = None) -> IngestResult:
    empty = RateBook(origin="APPROVED", tables={}, metadata={}, unmapped_sheets=())
    res = IngestResult(book=empty)
    files, tmp = _workbooks(Path(path), cfg, res)
    tables: dict[str, RateTable] = {}
    meta: dict[str, str] = {}
    unmapped: list[str] = []
    try:
        for f in files:
            display = re.sub(r"^\d{3}_", "", f.name) if tmp else f.name
            rep = safety_gate_workbook(f, cfg.limits)
            res.warnings.extend(w.replace(f.name, display) for w in rep.warnings)
            res.info.extend(w.replace(f.name, display) for w in rep.info)
            with WorkbookReader(f, cfg.limits, formatting=True) as rd:
                res.input_files.append({"name": display, "sha256": rd.sha256})
                inv = rd.inventory()
                res.inventory[display] = inv
                data_sheets = [s for s in inv if s.name.strip().upper() not in META_SHEETS]
                for s in inv:
                    if s.name.strip().upper() in META_SHEETS:
                        g = rd.grid(s.name)
                        md, _, _ = extract_metadata(g, len(g.rows), cfg.dates.order)
                        meta.update({k: v for k, v in md.items() if k not in meta})
                        unmapped.append(f"{display}!{s.name}")
                        continue
                    if s.visibility != "visible" and not (learned and s.name in learned.hidden_sheets_ok):
                        res.issues.append(SheetIssue(display, s.name, "HIDDEN", f"Sheet is {s.visibility}; inventoried but not mapped."))
                        res.warnings.append(f"{display}!{s.name}: hidden sheet not used (confirm via `rgl map` to include).")
                        unmapped.append(f"{display}!{s.name}")
                        continue
                    g = rd.grid(s.name)
                    layout = detect_approved_layout(g, cfg.limits.max_header_scan_rows)
                    if not isinstance(layout, ApprovedLayout):
                        res.issues.append(SheetIssue(display, s.name, "NEEDS_MAPPING", failure_reason(g, layout.reason)))
                        unmapped.append(f"{display}!{s.name}")
                        continue
                    if layout.multiple_tables:
                        res.issues.append(SheetIssue(display, s.name, "NEEDS_MAPPING", "Several tables on one sheet; confirm the regions with `rgl map`."))
                        unmapped.append(f"{display}!{s.name}")
                        continue
                    name = s.name
                    if _GENERIC.match(name) and len(data_sheets) == 1:
                        name = Path(display).stem
                    nid = canonical_id(lob, name)
                    if nid in tables:
                        res.issues.append(SheetIssue(display, s.name, "NEEDS_MAPPING", f"Table identity {nid} is used by more than one approved sheet."))
                        unmapped.append(f"{display}!{s.name}")
                        continue
                    t = build_table(
                        grid=g, file_name=display, sha256=rd.sha256, origin="APPROVED", native_id=nid,
                        display_name=name, header_row=layout.header_row, data_start=layout.data_start,
                        data_end=layout.data_end, declared_types=None, metadata={}, cfg=cfg, learned=learned,
                        extra_warnings=layout.notes + ([f"Rows above the header were skipped: {len(layout.skipped_rows)}"] if layout.skipped_rows else []),
                    )
                    tables[nid] = t
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
    # book-level metadata: prefer META sheet; table-level metadata is empty on the approved side
    res.book = RateBook(origin="APPROVED", tables=tables, metadata=meta, unmapped_sheets=tuple(unmapped))
    shas = [x["sha256"] for x in res.input_files]
    if len(set(shas)) < len(shas):
        res.warnings.append("Two approved files are byte-identical.")
    return res
