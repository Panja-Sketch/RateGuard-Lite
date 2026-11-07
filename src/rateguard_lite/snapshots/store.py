"""Snapshot History: the tool's own baseline (spec 8.7). Local SQLite, never leaves the machine."""

from __future__ import annotations

import json
import sqlite3
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import data_dir
from ..engines.common import table_hash
from ..errors import RGLError
from ..learning.store import current_user, now
from ..model.core import CellValue, ColumnRole, ColumnSpec, ColumnType, RateBook, RateTable, Row, SourceRef

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshot(
  id TEXT PRIMARY KEY, book_key TEXT NOT NULL, run_id TEXT, taken_at TEXT, edition TEXT, effective_date TEXT,
  status TEXT NOT NULL, accepted_by TEXT, accepted_at TEXT, export_name TEXT, export_sha TEXT, tables INTEGER, book_meta_json TEXT, verdict TEXT);
CREATE TABLE IF NOT EXISTS snapshot_table(
  snapshot_id TEXT NOT NULL, native_id TEXT NOT NULL, table_code TEXT, display_name TEXT, edition TEXT, effective_date TEXT,
  row_count INTEGER, sha TEXT, meta_json TEXT, blob BLOB, PRIMARY KEY(snapshot_id, native_id));
"""
_WILD, _BLANK = "\x00W", "\x00B"


def book_key(meta: dict[str, str]) -> str:
    return "|".join((meta.get("rate_book_code", "?"), meta.get("jurisdiction", "?"), meta.get("lob", "?")))


def _enc(c: CellValue) -> str | None:
    return _WILD if c.kind == "WILDCARD" else (_BLANK if c.kind == "BLANK" else c.value)


def _dec(v: str | None, coord: str) -> CellValue:
    if v == _WILD:
        return CellValue("WILDCARD", None, None, coord)
    if v == _BLANK:
        return CellValue("BLANK", None, None, coord)
    return CellValue("VALUE", v, v, coord)


@dataclass
class SnapshotInfo:
    id: str
    book_key: str
    run_id: str
    taken_at: str
    edition: str
    effective_date: str
    status: str
    accepted_by: str | None
    accepted_at: str | None
    tables: int
    verdict: str | None


class SnapshotStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else data_dir() / "snapshots.db"
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------ write
    def save(self, run_id: str, export: RateBook, export_name: str, export_sha: str, verdict: str | None = None,
             status: str = "UNVERIFIED", user: str | None = None, retention: int = 20) -> str:
        sid = f"S-{run_id}"
        meta = export.metadata
        key = book_key(meta)
        self.db.execute("DELETE FROM snapshot_table WHERE snapshot_id=?", (sid,))
        self.db.execute("DELETE FROM snapshot WHERE id=?", (sid,))
        self.db.execute("INSERT INTO snapshot VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (sid, key, run_id, now(), meta.get("edition", ""), meta.get("effective_date", ""), status,
                         (user or current_user()) if status == "VERIFIED" else None, now() if status == "VERIFIED" else None,
                         export_name, export_sha, len(export.tables), json.dumps(meta), verdict))
        rows = []
        for nid, t in export.tables.items():
            cols = [[c.canonical_name, c.source_header, c.role.value, c.type.value, c.scale] for c in t.columns]
            kcols = t.key_columns()
            fnames = [c.canonical_name for c in t.factor_columns()]
            body = [[_enc(c) for c in r.key] + [_enc(r.factors[n]) if n in r.factors else _BLANK for n in fnames] for r in t.rows]
            blob = zlib.compress(json.dumps({"cols": cols, "nk": len(kcols), "fnames": fnames, "rows": body}, separators=(",", ":")).encode(), 6)
            rows.append((sid, nid, t.metadata.get("table_code", ""), t.display_name, t.metadata.get("edition", ""),
                         t.metadata.get("effective_date", ""), len(t.rows), table_hash(t), json.dumps(t.metadata), blob))
        self.db.executemany("INSERT INTO snapshot_table VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        self.db.commit()
        self.purge(keep=retention, key=key)
        return sid

    def accept(self, sid: str, user: str | None = None) -> None:
        r = self.db.execute("SELECT verdict FROM snapshot WHERE id=?", (sid,)).fetchone()
        if r is None:
            raise RGLError(f"Snapshot {sid} not found.", next_step="Run `rgl snapshot list`.")
        if r["verdict"] in ("FAIL", "BLOCKED"):
            raise RGLError(f"Snapshot {sid} came from a {r['verdict']} run and cannot be accepted as a baseline.",
                           next_step="Fix the findings and run again.")
        self.db.execute("UPDATE snapshot SET status='VERIFIED', accepted_by=?, accepted_at=? WHERE id=?", (user or current_user(), now(), sid))
        self.db.commit()

    def purge(self, keep: int | None = None, key: str | None = None, all_: bool = False) -> int:
        if all_:
            n = self.db.execute("SELECT COUNT(*) FROM snapshot").fetchone()[0]
            self.db.execute("DELETE FROM snapshot_table")
            self.db.execute("DELETE FROM snapshot")
            self.db.commit()
            return int(n)
        keys = [key] if key else [r[0] for r in self.db.execute("SELECT DISTINCT book_key FROM snapshot")]
        removed = 0
        for k in keys:
            ids = [r[0] for r in self.db.execute("SELECT id FROM snapshot WHERE book_key=? ORDER BY taken_at DESC, id DESC", (k,))]
            for sid in ids[keep or 0:]:
                self.db.execute("DELETE FROM snapshot_table WHERE snapshot_id=?", (sid,))
                self.db.execute("DELETE FROM snapshot WHERE id=?", (sid,))
                removed += 1
        self.db.commit()
        return removed

    # ------------------------------------------------------------ read
    def list(self, key: str | None = None) -> list[SnapshotInfo]:
        q = "SELECT * FROM snapshot" + (" WHERE book_key=?" if key else "") + " ORDER BY taken_at DESC, id DESC"
        return [SnapshotInfo(r["id"], r["book_key"], r["run_id"], r["taken_at"], r["edition"], r["effective_date"], r["status"],
                             r["accepted_by"], r["accepted_at"], r["tables"], r["verdict"]) for r in self.db.execute(q, (key,) if key else ())]

    def get(self, sid: str) -> SnapshotInfo | None:
        return next((s for s in self.list() if s.id == sid), None)

    def select_baseline(self, key: str, edition: str | None, effective_date: str | None) -> SnapshotInfo | None:
        """Latest VERIFIED snapshot of this rate book that is older than the current filing (spec 8.7)."""
        for s in self.list(key):
            if s.status != "VERIFIED":
                continue
            older = (effective_date and s.effective_date and s.effective_date < effective_date) or \
                    (edition and s.edition and s.edition != edition)
            if older:
                return s
        return None

    def load_book(self, sid: str, current_hashes: dict[str, str]) -> RateBook:
        """Rebuild a baseline RateBook. Tables whose hash equals the current one become cheap stubs (fast path)."""
        info = self.db.execute("SELECT * FROM snapshot WHERE id=?", (sid,)).fetchone()
        if info is None:
            raise RGLError(f"Snapshot {sid} not found.")
        tables: dict[str, RateTable] = {}
        for r in self.db.execute("SELECT * FROM snapshot_table WHERE snapshot_id=?", (sid,)):
            meta: dict[str, Any] = json.loads(r["meta_json"])
            nid = r["native_id"]
            src = SourceRef("snapshot", nid, "", info["export_sha"] or "")
            if current_hashes.get(nid) == r["sha"]:
                meta = {**meta, "stub": "1", "hash": r["sha"]}
                tables[nid] = RateTable(nid, r["display_name"], "BASELINE", src, (), (), meta, "FULL")
                continue
            data = json.loads(zlib.decompress(r["blob"]))
            cols = tuple(ColumnSpec(c[0], c[1], ColumnRole(c[2]), ColumnType(c[3]), c[4]) for c in data["cols"])
            nk = data["nk"]
            rows = tuple(Row(tuple(_dec(v, "") for v in row[:nk]), {n: _dec(v, "") for n, v in zip(data["fnames"], row[nk:], strict=True)}, i + 1)
                         for i, row in enumerate(data["rows"]))
            tables[nid] = RateTable(nid, r["display_name"], "BASELINE", src, cols, rows, meta, "FULL")
        return RateBook("BASELINE", tables, json.loads(info["book_meta_json"]))
