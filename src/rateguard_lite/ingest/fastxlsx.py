"""Fast, safe, values-only .xlsx reader for large Guidewire exports (spec A6).

Uses lxml with entity resolution and network access disabled. Reads cached values only:
no formulas evaluated, no macros, no external links followed. Formatting is not needed on the
export side. openpyxl remains the reader for approved workbooks.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from lxml import etree

from ..config import LimitsCfg
from ..errors import UnsafeInputError
from .workbook import Grid, SheetInfo, sha256_file

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_EPOCH = datetime(1899, 12, 30)
_BUILTIN_DATE_IDS = set(range(14, 23)) | {45, 46, 47}
_COL_RE = re.compile(r"^([A-Z]+)(\d+)$")
_DATE_CODE_RE = re.compile(r"[dmyhs]", re.I)


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _parser() -> Any:
    return etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, load_dtd=False)


def _iterparse(fh: Any, tag: str) -> Any:
    return etree.iterparse(fh, events=("end",), tag=tag, resolve_entities=False, no_network=True,
                           huge_tree=False, load_dtd=False)


class FastWorkbookReader:
    formatting = False

    def __init__(self, path: Path, limits: LimitsCfg) -> None:
        self.path = Path(path)
        self.limits = limits
        self.sha256 = sha256_file(self.path)
        try:
            self._zf = zipfile.ZipFile(self.path)
            self._sheets = self._read_workbook()
            self._shared = self._read_shared_strings()
            self._date_styles = self._read_styles()
        except (zipfile.BadZipFile, etree.XMLSyntaxError, KeyError) as exc:
            raise UnsafeInputError(f"Workbook could not be read: {type(exc).__name__}.", where=self.path.name,
                                   next_step="Open it in Excel and re-save as a plain .xlsx.") from exc
        self._cells_read = 0

    def close(self) -> None:
        self._zf.close()

    def __enter__(self) -> FastWorkbookReader:
        return self

    def __exit__(self, *a: object) -> None:
        self.close()

    # ------------------------------------------------------------ package parts
    def _read_workbook(self) -> list[tuple[str, str, str]]:
        root = etree.fromstring(self._zf.read("xl/workbook.xml"), _parser())
        rels = etree.fromstring(self._zf.read("xl/_rels/workbook.xml.rels"), _parser())
        targets = {r.get("Id"): r.get("Target") for r in rels.iter(f"{_PKG_REL}Relationship")}
        out = []
        for sh in root.iter(f"{_NS}sheet"):
            tgt = targets.get(sh.get(f"{_REL}id"), "")
            path = tgt.lstrip("/") if tgt.startswith("/") else posixpath.normpath(posixpath.join("xl", tgt))
            out.append((sh.get("name", ""), sh.get("state", "visible"), path))
        return out

    def _read_shared_strings(self) -> list[str]:
        if "xl/sharedStrings.xml" not in self._zf.namelist():
            return []
        out: list[str] = []
        with self._zf.open("xl/sharedStrings.xml") as fh:
            for _, si in _iterparse(fh, f"{_NS}si"):
                parts = []
                for t in si.iter(f"{_NS}t"):
                    par = t.getparent()
                    if par is not None and par.tag == f"{_NS}rPh":
                        continue
                    parts.append(t.text or "")
                out.append("".join(parts))
                si.clear()
        return out

    def _read_styles(self) -> set[int]:
        if "xl/styles.xml" not in self._zf.namelist():
            return set()
        root = etree.fromstring(self._zf.read("xl/styles.xml"), _parser())
        custom: dict[int, str] = {}
        for nf in root.iter(f"{_NS}numFmt"):
            custom[int(nf.get("numFmtId", "0"))] = nf.get("formatCode", "")
        date_styles: set[int] = set()
        xfs = root.find(f"{_NS}cellXfs")
        if xfs is not None:
            for i, xf in enumerate(xfs.findall(f"{_NS}xf")):
                fid = int(xf.get("numFmtId", "0"))
                if fid in _BUILTIN_DATE_IDS:
                    date_styles.add(i)
                elif fid in custom:
                    code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", custom[fid])
                    if _DATE_CODE_RE.search(code):
                        date_styles.add(i)
        return date_styles

    # ------------------------------------------------------------ public API (mirrors WorkbookReader)
    def sheet_names(self) -> list[str]:
        return [n for n, _, _ in self._sheets]

    def inventory(self) -> list[SheetInfo]:
        return [SheetInfo(name=n, visibility=st, tab_color=None, used_range="", merged=[], row_count=0, col_count=0)
                for n, st, _ in self._sheets]

    def grid(self, name: str) -> Grid:
        path = next(p for n, _, p in self._sheets if n == name)
        state = next(s for n, s, _ in self._sheets if n == name)
        rows: list[tuple[Any, ...]] = []
        shared, dstyles = self._shared, self._date_styles
        max_cells = self.limits.max_cells
        with self._zf.open(path) as fh:
            for _, row in _iterparse(fh, f"{_NS}row"):
                rnum = int(row.get("r", len(rows) + 1))
                while len(rows) < rnum - 1:
                    rows.append(())
                vals: list[Any] = []
                for c in row:
                    ref = c.get("r")
                    ci = _col_index(_COL_RE.match(ref).group(1)) if ref else len(vals)  # type: ignore[union-attr]
                    while len(vals) < ci:
                        vals.append(None)
                    t = c.get("t")
                    v: Any = None
                    if t == "inlineStr":
                        v = "".join(x.text or "" for x in c.iter(f"{_NS}t"))
                    else:
                        ve = c.find(f"{_NS}v")
                        txt = ve.text if ve is not None else None
                        if txt is not None:
                            if t == "s":
                                v = shared[int(txt)]
                            elif t in ("str", "e"):
                                v = txt
                            elif t == "b":
                                v = txt == "1"
                            elif t == "d":
                                v = datetime.fromisoformat(txt)
                            else:
                                num = float(txt) if ("." in txt or "E" in txt or "e" in txt) else int(txt)
                                s_attr = c.get("s")
                                if s_attr is not None and int(s_attr) in dstyles:
                                    v = _EPOCH + timedelta(days=float(num))
                                else:
                                    v = num
                    vals.append(v)
                rows.append(tuple(vals))
                self._cells_read += len(vals)
                if self._cells_read > max_cells:
                    raise UnsafeInputError(f"Workbook exceeds the {max_cells:,}-cell limit.",
                                           where=f"{self.path.name}!{name}",
                                           next_step="Split the workbook or raise limits.max_cells (hard max 10,000,000).")
                row.clear()
        while rows and all(v is None or (isinstance(v, str) and not v.strip()) for v in rows[-1]):
            rows.pop()
        return Grid(name=name, rows=rows, visibility=state)
