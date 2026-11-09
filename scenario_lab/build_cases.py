"""Scenario Lab - synthetic input builder.

Reuses the product's fixture generator (table model `T`, Guidewire-style export writer, world builder)
and adds new, case-specific tables, defects and file writers. Synthetic data only. Nothing under src/
is changed.

Usage:  python scenario_lab/build_cases.py [--only H1 --only F6] [--out scenario_lab/out]
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import re
import shutil
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import openpyxl
import yaml
from openpyxl.styles import Font

from rateguard_lite.samples.generator import (
    NEW_EDITION,
    NEW_EFF,
    OLD_EDITION,
    OLD_EFF,
    TICKET,
    T,
    build_world,
    q,
    write_export,
)

HERE = Path(__file__).resolve().parent
OLD_STAMP = OLD_EFF.strftime("%m/%d/%Y")


# ------------------------------------------------------------------ small helpers
def slug(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", title.title())


def fac(rng: random.Random, scale: int = 4, lo: float = 0.6, hi: float = 1.7) -> Decimal:
    return q(rng.uniform(lo, hi), scale)


def terr(n: int, width: int = 3) -> list[str]:
    return [f"{i:0{width}d}" for i in range(1, n + 1)]


def scale_rows(t: T, mult: str, col: int = -1) -> None:
    sc = t.factors[col][2] if t.factors else 4
    for r in t.rows:
        if r[col] is not None and r[col] != "":
            r[col] = q(Decimal(str(r[col])) * Decimal(mult), sc)


@dataclass
class Fmt:
    """A cell value with an Excel number format (used for ZIP codes stored as numbers)."""
    value: Any
    fmt: str


@dataclass
class Entry:
    num: int
    title: str
    old: T | None
    new: T
    export: T
    approved: T | None
    jira: str | None                                   # update | new | bypass | struck | None
    xf: Callable[[list[Any]], list[Any]] | None = None  # approved-side cell re-writer
    post: Callable[[Any], None] | None = None           # worksheet hook (formulas, merges, strike...)
    sheet: str | None = None                            # approved tab name override
    extra_sheet: bool = False                           # add a hidden "archive" sheet (E1)

    def apply(self, fn: Callable[[T], None]) -> Entry:
        """An intended change: applied to the new state, the export and the approved file alike."""
        for t in (self.new, self.export, self.approved):
            if t is not None:
                fn(t)
        return self


class Lab:
    def __init__(self, cid: str, titles: dict[int, str], n_tables: int, seed: int, state: str = "OR", lob: str = "BOP"):
        self.cid, self.titles, self.state, self.lob = cid, titles, state, lob
        self.rng = random.Random(seed * 1000 + sum(map(ord, cid)))
        self.world: list[T] = build_world(n_tables, seed)
        self.entries: dict[int, Entry] = {}
        self.export_edition, self.export_eff = NEW_EDITION, NEW_EFF
        self.meta: dict[str, dict[str, str]] = {}
        self.approved_kind = "xlsx"
        self.jira_text: str | None = None
        self.n_tables = n_tables

    # -- table construction ------------------------------------------------
    def _t(self, num: int, keys: list[tuple[str, str]], factors: list[tuple[str, str, int]], rows: list[list[Any]]) -> T:
        title = self.titles[num]
        name = f"{num} {title}"
        return T(f"LAB{num}_{slug(title)}", name, num, "named", keys, factors, rows,
                 [k[0] for k in keys], [f[0] for f in factors], name)

    def add(self, num: int, keys: list[tuple[str, str]], factors: list[tuple[str, str, int]], rows: list[list[Any]],
            jira: str | None = "update", approved: bool = True, **kw: Any) -> Entry:
        old = self._t(num, keys, factors, rows)
        e = Entry(num, self.titles[num], old, old.copy(), old.copy(), old.copy() if approved else None, jira, **kw)
        self.entries[num] = e
        return e

    def add_new(self, num: int, keys: list[tuple[str, str]], factors: list[tuple[str, str, int]], rows: list[list[Any]], **kw: Any) -> Entry:
        t = self._t(num, keys, factors, rows)
        e = Entry(num, self.titles[num], None, t.copy(), t.copy(), t.copy(), "new", **kw)
        self.entries[num] = e
        return e

    # -- outputs -----------------------------------------------------------
    def export_tables(self) -> list[T]:
        return [*self.world, *(e.export for e in self.entries.values())]

    def baseline_tables(self) -> list[T]:
        return [*self.world, *(e.old for e in self.entries.values() if e.old is not None)]

    def meta_for(self, tables: list[T]) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for t in tables:
            m: dict[str, str] = {}
            if self.state != "OR":
                m["Jurisdiction"] = self.state
            if self.lob != "BOP":
                m["Line of Business"] = self.lob
            m.update(self.meta.get(t.code, {}))
            if m:
                out[t.code] = m
        return out

    def jira(self) -> str:
        if self.jira_text is not None:
            return self.jira_text
        upd, new, byp = [], [], []
        for e in self.entries.values():
            line = f"Table {e.num} {e.title}"
            if e.jira == "update":
                upd.append(f"- {line}")
            elif e.jira == "struck":
                upd.append(f"- ~~{line}~~")
            elif e.jira == "new":
                new.append(f"- {line}")
            elif e.jira == "bypass":
                byp.append(f"- {line} - DO NOT LOAD")
        parts = [f"{TICKET}: {self.state} {self.lob} rate revision (scenario lab {self.cid})", "",
                 f"LOB: {self.lob}", f"State: {self.state}", "Filing Effective Date: 03/15/2027",
                 "Renewal Effective Date: 05/15/2027", f"Rate Book Edition: {NEW_EDITION}",
                 "Applies to: New Business and Renewal transactions", ""]
        if upd:
            parts += ["Tables to update:", *upd, ""]
        if new:
            parts += ["New tables:", *new, ""]
        if byp:
            parts += ["Bypass:", *byp, ""]
        parts += ["Comments:", "Actuarial approved the attached workbook.", ""]
        return "\n".join(parts)


# ------------------------------------------------------------------ approved-file writers
def _cell(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if v == "" or v is None:
        return None
    return v


def _write_sheet(ws: Any, e: Entry) -> None:
    t = e.approved
    assert t is not None
    ws.append(list(t.approved_keys) + list(t.approved_factors))
    rows = sorted(t.rows, key=lambda x: tuple(str(c) for c in x[: t.nk()]))
    for r in rows:
        vals = e.xf(list(r)) if e.xf else list(r)
        ws.append([(_cell(v.value) if isinstance(v, Fmt) else _cell(v)) for v in vals])
        for j, v in enumerate(vals, start=1):
            if isinstance(v, Fmt):
                ws.cell(row=ws.max_row, column=j).number_format = v.fmt
    if e.post:
        e.post(ws)


def _sheet_name(e: Entry) -> str:
    assert e.approved is not None
    return (e.sheet or e.approved.approved_sheet)[:31]


def write_approved_book(path: Path, entries: list[Entry]) -> None:
    wb = openpyxl.Workbook()
    first = wb.active
    assert first is not None
    wb.remove(first)
    for e in entries:
        _write_sheet(wb.create_sheet(_sheet_name(e)), e)
    wb.save(path)


def write_approved_zip(path: Path, entries: list[Entry], tmp: Path) -> None:
    tmp.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for e in entries:
            wb = openpyxl.Workbook()
            first = wb.active
            assert first is not None
            wb.remove(first)
            _write_sheet(wb.create_sheet(_sheet_name(e)), e)
            if e.extra_sheet:
                hid = wb.create_sheet("Old Draft (archive)")
                hid.append(["Note", "Superseded draft - ignore"])
                hid.append(["Territory", "Draft Factor"])
                hid.append(["001", 9.9999])
                hid.sheet_state = "hidden"
            fp = tmp / f"_{e.num}.xlsx"
            wb.save(fp)
            fname = re.sub(r"[^A-Za-z0-9 ._()-]+", "_", f"{_sheet_name(e)}.xlsx")
            zf.write(fp, fname)
            fp.unlink()


# ------------------------------------------------------------------ case builders
def b_h1(L: Lab) -> None:
    r = L.rng
    L.add(501, [("Territory", "STRING")], [("Base Rate Factor", "DECIMAL", 4)], [[t, fac(r)] for t in terr(40)]).apply(lambda t: scale_rows(t, "1.05"))
    cov = ["BLD", "BPP", "BI", "EE", "GL", "EPLI", "CRIME", "EQ", "FLOOD", "UTILITY"]
    L.add(502, [("Coverage", "STRING")], [("Multiplier Factor", "DECIMAL", 4)], [[c, fac(r, 4, 0.8, 1.3)] for c in cov]).apply(lambda t: scale_rows(t, "1.03"))
    rows = [[t, f"{c:04d}", f"CONSTR{k}", fac(r, 4, 0.5, 2.0)] for t in terr(60) for c in range(100, 140) for k in range(6)]
    L.add(503, [("Territory", "STRING"), ("Class Code", "STRING"), ("Construction", "STRING")], [("Loss Cost Factor", "DECIMAL", 4)], rows).apply(lambda t: scale_rows(t, "1.025"))


def b_h2(L: Lab) -> None:
    r = L.rng
    e = L.add(504, [("Census Block", "STRING")], [("Census Factor", "DECIMAL", 6)], [[f"06{i:010d}", fac(r, 6, 0.5, 1.9)] for i in range(1500)])
    e.apply(lambda t: [row.__setitem__(-1, fac(r, 6, 0.5, 1.9)) for row in t.rows])
    # approved/new keep the same 6-decimal values: re-sync them from the approved copy
    assert e.approved is not None
    for tt in (e.new, e.export):
        for ra, rt in zip(e.approved.rows, tt.rows, strict=True):
            rt[-1] = ra[-1]
    e2 = L.add(505, [("Class Code", "STRING")], [("High Precision Factor", "DECIMAL", 8)], [[f"{c:04d}", fac(r, 8, 0.5, 1.9)] for c in range(500, 540)])
    e2.apply(lambda t: [row.__setitem__(-1, fac(r, 8, 0.5, 1.9)) for row in t.rows])
    assert e2.approved is not None
    for tt in (e2.new, e2.export):
        for ra, rt in zip(e2.approved.rows, tt.rows, strict=True):
            rt[-1] = ra[-1]
    for ex, h in ((e.export, "Census Factor"), (e2.export, "High Precision Factor")):
        ex.factors = [(h, "DECIMAL", 4)]
        for row in ex.rows:
            row[-1] = q(row[-1], 4)


def b_h3(L: Lab) -> None:
    r = L.rng
    L.add_new(506, [("Cyber Limit", "INTEGER")], [("Cyber Factor", "DECIMAL", 4)], [[v, fac(r)] for v in (50000, 100000, 250000, 500000, 1000000)])
    e = L.add(507, [("Class Code", "STRING")], [("Class Factor", "DECIMAL", 4)], [[f"{c:04d}", fac(r)] for c in range(100, 130)])
    new_rows = [[f"{c:04d}", fac(r)] for c in (201, 202, 203)]
    e.apply(lambda t: t.rows.extend(copy.deepcopy(new_rows)))
    e = L.add(508, [("Deductible", "INTEGER")], [("Deductible Factor", "DECIMAL", 3)], [[d, q(1.2 - 0.05 * i, 3)] for i, d in enumerate((250, 500, 1000, 2500, 5000))])
    e.apply(lambda t: t.rows.extend([[7500, Decimal("0.900")], [10000, Decimal("0.850")]]))
    e = L.add(509, [("PD Deductible", "INTEGER")], [("PD Deductible Factor", "DECIMAL", 3)], [[d, q(1.15 - 0.06 * i, 3)] for i, d in enumerate((500, 1000, 2500, 5000))])
    e.apply(lambda t: t.rows.append([1500, Decimal("1.040")]))
    rows = [[lim, tier, fac(r, 4, 0.7, 1.5)] for lim in (100000, 250000, 500000, 1000000) for tier in ("Tier 1", "Tier 2", "Tier 3")]
    L.add_new(510, [("EPL Limit", "INTEGER"), ("Employee Tier", "STRING")], [("EPL Factor", "DECIMAL", 4)], rows)


def b_h4(L: Lab) -> None:
    r = L.rng
    e = L.add(511, [("Multi Policy Discount", "BOOLEAN")], [("Discount Factor", "DECIMAL", 4)], [["TRUE", Decimal("0.9200")], ["FALSE", Decimal("1.0000")]])
    e.apply(lambda t: scale_rows(t, "1.01"))
    e.xf = lambda row: ["Yes" if row[0] == "TRUE" else "No", *row[1:]]
    e = L.add(512, [("Package Credit Applies", "BOOLEAN")], [("Credit Factor", "DECIMAL", 4)], [[1, Decimal("0.9500")], [0, Decimal("1.0000")]])
    e.apply(lambda t: scale_rows(t, "1.01"))
    e.xf = lambda row: [bool(row[0]), *row[1:]]
    codes = [f"{c:04d}" for c in range(300, 308)]
    e = L.add(513, [("Class Code", "STRING")], [("Class Factor", "DECIMAL", 4)], [[c, fac(r)] for c in codes] + [["", fac(r)]])
    e.apply(lambda t: scale_rows(t, "1.02"))
    e.xf = lambda row: ["*" if row[0] == "" else row[0], *row[1:]]
    e = L.add(514, [("Class Code", "STRING")], [("Class Factor", "DECIMAL", 4)], [[c, fac(r)] for c in codes] + [["*", fac(r)]])
    e.apply(lambda t: scale_rows(t, "1.02"))
    e.xf = lambda row: ["ALL" if row[0] == "*" else row[0], *row[1:]]
    e = L.add(515, [("Class Code", "STRING")], [("Class Factor", "DECIMAL", 4)], [[c, fac(r)] for c in codes] + [["ALL", fac(r)]])
    e.apply(lambda t: scale_rows(t, "1.02"))
    e.xf = lambda row: ["" if row[0] == "ALL" else row[0], *row[1:]]
    zips = ["01234", "01235", "02101", "06103", "07030", "10001", "20001", "33101"]
    e = L.add(516, [("Zip Code", "STRING")], [("Zip Factor", "DECIMAL", 4)], [[z, fac(r)] for z in zips])
    e.apply(lambda t: scale_rows(t, "1.02"))
    e.xf = lambda row: [Fmt(int(row[0]), "00000"), *row[1:]]
    ppc = [str(i) for i in range(1, 11)] + ["8B", "9B"]
    e = L.add(517, [("Protection Class", "STRING")], [("Protection Factor", "DECIMAL", 4)], [[p, fac(r)] for p in ppc])
    e.apply(lambda t: scale_rows(t, "1.02"))
    e.xf = lambda row: [int(row[0]) if str(row[0]).isdigit() else row[0], *row[1:]]


def b_h5(L: Lab) -> None:
    r = L.rng
    L.add(518, [("Territory", "STRING")], [("Fire Alarm Factor", "DECIMAL", 4)], [[t, fac(r)] for t in terr(20)]).apply(lambda t: scale_rows(t, "0.97"))
    L.add(519, [("Territory", "STRING")], [("Sprinkler Factor", "DECIMAL", 4)], [[t, fac(r)] for t in terr(20)]).apply(lambda t: scale_rows(t, "0.95"))


def b_f1(L: Lab) -> None:
    r = L.rng
    e = L.add(520, [("Territory", "STRING")], [("Base Rate Factor", "DECIMAL", 4)], [[t, fac(r)] for t in terr(15)])
    e.apply(lambda t: scale_rows(t, "1.05"))
    e.export.rows[4][-1] = q(Decimal(str(e.export.rows[4][-1])) * Decimal("1.08"), 4)
    e = L.add(521, [("Territory", "STRING")], [("Base Rate Factor", "DECIMAL", 4)], [[t, fac(r, 4, 0.004, 0.009)] for t in terr(12)])
    e.apply(lambda t: scale_rows(t, "1.10"))
    for t in (e.new, e.approved):
        assert t is not None
        t.rows[3][-1] = Decimal("0.0050")
    e.export.rows[3][-1] = Decimal("0.0005")
    e = L.add(522, [("Territory", "STRING")], [("Base Rate Factor", "DECIMAL", 4)], [[t, fac(r)] for t in terr(12)])
    e.apply(lambda t: scale_rows(t, "1.04"))
    for t in (e.new, e.approved):
        assert t is not None
        t.rows[5][-1] = Decimal("1.2345")
    e.export.rows[5][-1] = Decimal("1.2354")
    e = L.add(523, [("Territory", "STRING")], [("Base Rate Factor", "DECIMAL", 4)], [[t, fac(r)] for t in terr(12)])
    e.apply(lambda t: scale_rows(t, "1.04"))
    e.export.rows[6][-1] = None
    e = L.add(524, [("BCEG Grade", "INTEGER")], [("BCEG Factor", "DECIMAL", 4)], [[g, q(0.70 + 0.05 * (g - 1), 4)] for g in range(1, 11)])
    e.apply(lambda t: scale_rows(t, "1.02"))
    e.export.rows[0][-1], e.export.rows[9][-1] = e.export.rows[9][-1], e.export.rows[0][-1]
    e = L.add(525, [("Coinsurance", "INTEGER")], [("Coinsurance Factor", "DECIMAL", 4)], [[80, Decimal("1.0500")], [90, Decimal("1.0000")], [100, Decimal("0.9500")]])
    e.apply(lambda t: scale_rows(t, "0.98"))
    e.export.rows[1][-1] = Decimal("1.0300")
    e = L.add(526, [("AI Type", "STRING")], [("Fee Amount", "INTEGER", 0)], [["Designated Person", 50], ["Managers or Lessors", 75], ["Mortgagee", 100]])
    e.apply(lambda t: [row.__setitem__(-1, row[-1] + 10) for row in t.rows])
    e.export.rows[1][-1] = 95
    e = L.add(527, [("Exclusion Applies", "BOOLEAN")], [("Exclusion Factor", "DECIMAL", 3)], [["Y", Decimal("0.900")], ["N", Decimal("1.000")]])
    e.apply(lambda t: scale_rows(t, "0.99"))
    e.export.rows[0][-1] = Decimal("0.950")


def b_f2(L: Lab) -> None:
    r = L.rng
    codes = [f"{c:04d}" for c in range(1000, 4000)]
    e = L.add(530, [("Class Code", "STRING")], [("Class Factor", "DECIMAL", 4)], [[c, fac(r)] for c in codes])
    changed = sorted(r.sample(range(len(codes)), 30))
    for i in changed:
        for t in (e.new, e.export, e.approved):
            assert t is not None
            t.rows[i][-1] = q(Decimal(str(t.rows[i][-1])) * Decimal("1.05"), 4)
    e.export.rows[changed[7]][-1] = q(Decimal(str(e.approved.rows[changed[7]][-1] if e.approved else 1)) * Decimal("1.09"), 4)   # wrong change
    untouched = next(i for i in range(1500, 3000) if i not in changed)
    e.export.rows[untouched][-1] = q(Decimal(str(e.export.rows[untouched][-1])) + Decimal("0.0200"), 4)         # altered by mistake


def b_f3(L: Lab) -> None:
    r = L.rng
    cls = [f"{c:04d}" for c in range(100, 112)]
    e = L.add(540, [("Class Code", "STRING")], [("Class Factor", "DECIMAL", 4)], [[c, fac(r)] for c in cls])
    e.apply(lambda t: scale_rows(t, "1.02"))
    e.apply(lambda t: t.rows.append(["0301", Decimal("1.1100")]))
    e.export.rows = [row for row in e.export.rows if row[0] != "0301"]
    e = L.add(541, [("Class Code", "STRING")], [("Class Factor", "DECIMAL", 4)], [[c, fac(r)] for c in cls])
    e.apply(lambda t: scale_rows(t, "1.02"))
    for t in (e.new, e.approved):
        assert t is not None
        t.rows = [row for row in t.rows if row[0] != "0105"]                                   # discontinued in the approved file
    # the export keeps the (re-rated) discontinued code
    e = L.add(542, [("Deductible", "INTEGER")], [("Deductible Factor", "DECIMAL", 3)], [[d, q(1.2 - 0.05 * i, 3)] for i, d in enumerate((250, 500, 1000, 2500, 5000))])
    e.apply(lambda t: scale_rows(t, "1.02"))
    e.apply(lambda t: t.rows.append([7500, Decimal("0.900")]))
    e.export.rows = [row for row in e.export.rows if row[0] != 7500]
    e = L.add(543, [("Territory", "STRING")], [("Duplicate Factor", "DECIMAL", 4)], [[t, fac(r)] for t in terr(10)])
    e.apply(lambda t: scale_rows(t, "1.02"))
    dup = list(e.export.rows[0])
    dup[-1] = dup[-1] + Decimal("0.0100")
    e.export.rows.append(dup)
    e = L.add(544, [("PD Deductible", "INTEGER")], [("PD Deductible Factor", "DECIMAL", 3)], [[d, q(1.15 - 0.06 * i, 3)] for i, d in enumerate((500, 1000, 2500, 5000))])
    e.apply(lambda t: scale_rows(t, "1.02"))
    e.apply(lambda t: t.rows.append([1500, Decimal("1.040")]))
    e.export.rows = [row for row in e.export.rows if row[0] != 1500]


def b_f4(L: Lab) -> None:
    r = L.rng
    e = L.add(550, [("Class Code", "STRING")], [("BLD Factor", "DECIMAL", 4), ("BPP Factor", "DECIMAL", 4)],
              [[f"{c:04d}", fac(r), fac(r)] for c in range(100, 120)])
    e.apply(lambda t: [(row.__setitem__(-2, q(Decimal(str(row[-2])) * Decimal("1.03"), 4)), row.__setitem__(-1, q(Decimal(str(row[-1])) * Decimal("1.03"), 4))) for row in t.rows])
    for row in e.export.rows:
        row[-1] = row[-2]                                                                        # BLD loaded into the BPP column
    tiers = (250, 500, 1000, 2500, 5000)
    ad = L.add(551, [("Deductible", "INTEGER")], [("Deductible Factor", "DECIMAL", 4)], [[d, fac(r, 4, 0.8, 1.3)] for d in tiers])
    ad.apply(lambda t: scale_rows(t, "1.02"))
    for num in (552, 553, 554):
        x = L.add(num, [("Deductible", "INTEGER")], [("Deductible Factor", "DECIMAL", 4)], [[d, fac(r, 4, 0.8, 1.3)] for d in tiers])
        x.apply(lambda t: scale_rows(t, "1.02"))
        for rx, ra in zip(x.export.rows, ad.export.rows, strict=True):
            rx[-1] = ra[-1]                                                                      # Cov A-D values pasted in


def b_f5(L: Lab) -> None:
    r = L.rng
    bands = [(1, 1), (2, 3), (4, 6), (7, 10), (11, 20), (21, "")]
    e = L.add(560, [("Stories From", "INTEGER"), ("Stories To", "INTEGER")], [("Stories Factor", "DECIMAL", 4)],
              [[a, b, q(1.0 + 0.0125 * i, 4)] for i, (a, b) in enumerate(bands)])
    e.apply(lambda t: scale_rows(t, "1.01"))
    e.export.rows[2][1], e.export.rows[3][0] = 7, 8
    yb = [(0, 1949), (1950, 1969), (1970, 1989), (1990, "")]
    rows = [[z, c, a, b, fac(r)] for z in ("Z1", "Z2", "Z3", "Z4") for c in ("FRAME", "MASONRY", "STEEL") for a, b in yb]
    e = L.add(561, [("EQ Zone", "STRING"), ("Construction", "STRING"), ("Year Built From", "INTEGER"), ("Year Built To", "INTEGER")], [("EQ Factor", "DECIMAL", 4)], rows)
    e.apply(lambda t: scale_rows(t, "1.02"))
    for row in e.export.rows:
        if row[0] == "Z2" and row[1] == "MASONRY" and row[2] == 1970:
            row[2] = 1971                                                                         # gap: 1970 not covered
    tb = [(0, 0), (1, 2), (3, 5), (6, "")]
    e = L.add(562, [("Tenure From", "INTEGER"), ("Tenure To", "INTEGER")], [("Tenure Factor", "DECIMAL", 4)], [[a, b, q(1.0 - 0.02 * i, 4)] for i, (a, b) in enumerate(tb)])
    e.apply(lambda t: scale_rows(t, "1.01"))
    e.export.rows[2][1] = 6                                                                        # 3-6 overlaps 6+
    pb = [(0, 4999), (5000, 9999), (10000, "")]
    e = L.add(563, [("Premium From", "INTEGER"), ("Premium To", "INTEGER")], [("MRD Credit Factor", "DECIMAL", 4)], [[a, b, q(1.0 - 0.03 * i, 4)] for i, (a, b) in enumerate(pb)])
    e.apply(lambda t: scale_rows(t, "1.01"))
    e.export.rows[0][1], e.export.rows[1][0] = 5000, 5001                                          # threshold moved by one
    vb = [(1990, 2005), (2006, 2015), (2016, "")]
    rows = [[s, a, b, fac(r, 4, 0.8, 1.4)] for s in ("S1", "S2", "S3", "S4") for a, b in vb]
    e = L.add(564, [("Vehicle Symbol", "STRING"), ("Model Year From", "INTEGER"), ("Model Year To", "INTEGER")], [("Symbol Factor", "DECIMAL", 4)], rows)
    e.apply(lambda t: scale_rows(t, "1.02"))
    for row in e.export.rows:
        if row[0] == "S3" and row[1] == 2016:
            row[1] = 2017                                                                         # 2016 not covered


def b_f6(L: Lab) -> None:
    r = L.rng
    limits = [100000, 300000, 500000, 1000000, 2000000]
    e = L.add(570, [("Limit", "INTEGER")], [("ILF Factor", "DECIMAL", 4)], [[v, fac(r, 4, 0.8, 1.4)] for v in limits])
    e.apply(lambda t: scale_rows(t, "1.02"))
    e.xf = lambda row: [f"${row[0]:,}", *row[1:]]
    e = L.add(571, [("Limit", "INTEGER")], [("ILF Factor", "DECIMAL", 4)], [[v, fac(r, 4, 0.8, 1.4)] for v in limits])
    e.apply(lambda t: scale_rows(t, "1.02"))
    short = {100000: "100k", 300000: "300k", 500000: "500k", 1000000: "1M", 2000000: "2M"}
    e.xf = lambda row: [short[row[0]], *row[1:]]
    rows = [[pt, cv, fac(r)] for pt in ("CMP_MONO", "CMP_PKG") for cv in ("BLD", "BPP", "BI", "EE", "LIAB")]
    e = L.add(572, [("Policy Type", "STRING"), ("Coverage", "STRING")], [("Split Factor", "DECIMAL", 4)], rows)
    e.apply(lambda t: scale_rows(t, "1.02"))
    e.xf = lambda row: [{"CMP_MONO": "Commercial", "CMP_PKG": "Package"}[row[0]], *row[1:]]
    e = L.add(573, [("Class Prefix", "STRING")], [("Prefix Factor", "DECIMAL", 4)], [[f"ccp_{c}", fac(r)] for c in range(100, 112)])
    e.apply(lambda t: scale_rows(t, "1.02"))
    for row in e.export.rows:
        if row[0] == "ccp_105":
            row[0] = "ccpl_105"
    names = ["Leak Sensor W/Appliance", "Leak Sensor W/O Appliance", "Auto Shutoff Valve", "Sprinkler Monitor", "Central Alarm"]
    e = L.add(574, [("Mitigation Device", "STRING")], [("Discount Factor", "DECIMAL", 4)], [[n, fac(r, 4, 0.8, 1.0)] for n in names])
    e.apply(lambda t: scale_rows(t, "0.99"))
    for row in e.export.rows:
        if row[0] == "Leak Sensor W/Appliance":
            row[0] = "Leak Sensor W/Application"
    e = L.add(575, [("Count Min", "INTEGER"), ("Count Max", "INTEGER")], [("Count Factor", "DECIMAL", 4)], [[0, 49, Decimal("1.0000")], [50, 99, Decimal("0.9700")], [100, "", Decimal("0.9400")]])
    e.apply(lambda t: scale_rows(t, "1.01"))
    assert e.approved is not None
    e.approved.approved_keys = ["Policy Count"]
    e.xf = lambda row: [">= 100" if row[1] == "" else f"{row[0]} to {row[1]}", row[2]]
    ul = [1000000, 2000000]
    rows = [[u, ln, st, fac(r, 4, 0.8, 1.4)] for u in ul for ln in ("CA", "GL", "EL") for st in ("CA", "OR", "WA")]
    e = L.add(576, [("Underlying Limit", "INTEGER"), ("Line", "STRING"), ("State", "STRING")], [("Umbrella Factor", "DECIMAL", 4)], rows)
    e.apply(lambda t: scale_rows(t, "1.03"))


def b_f7(L: Lab) -> None:
    r = L.rng
    for num, nm, jira, ap in ((580, "Bypassed", "bypass", True), (581, "Deprecated", "struck", False), (582, "Untargeted", None, False)):
        e = L.add(num, [("Territory", "STRING")], [(f"{nm} Factor", "DECIMAL", 4)], [[t, fac(r)] for t in terr(8)], jira=jira, approved=ap)
        e.export.rows[2][-1] = q(Decimal(str(e.export.rows[2][-1])) * Decimal("1.20"), 4)         # changed although it should not be
    L.add(583, [("Territory", "STRING")], [("Declared Factor", "DECIMAL", 4)], [[t, fac(r)] for t in terr(8)]).apply(lambda t: scale_rows(t, "1.03"))
    # About 40% of the untouched tables keep their old edition/date stamp, the rest carry this filing's stamp
    # (same pattern as the product's own out_of_scope_table_dated fixture).
    old_ones = L.world[: max(1, len(L.world) * 2 // 5)]
    for t in old_ones:
        L.meta[t.code] = {"Edition": OLD_EDITION, "Effective Date": OLD_STAMP}


def b_f8(L: Lab) -> None:
    r = L.rng
    L.add(585, [("Territory", "STRING")], [("Loss Cost Factor", "DECIMAL", 4)], [[t, fac(r)] for t in terr(40)]).apply(lambda t: scale_rows(t, "1.02"))
    L.export_edition, L.export_eff = OLD_EDITION, date(2027, 4, 1)


def b_f9(L: Lab) -> None:
    r = L.rng
    rng_az = random.Random(99)
    spec = {590: ("Base Rate", [("Territory", "STRING")], terr(20)), 591: ("Territory", [("Territory", "STRING")], terr(25)),
            592: ("Class", [("Class Code", "STRING")], [f"{c:04d}" for c in range(100, 125)]),
            593: ("Deductible", [("Deductible", "INTEGER")], [250, 500, 1000, 2500, 5000]), 594: ("Stories", [("Stories", "INTEGER")], list(range(1, 11))),
            595: ("Protection", [("Protection Class", "STRING")], [str(i) for i in range(1, 11)]), 596: ("Fire", [("Territory", "STRING")], terr(10))}
    for num, (nm, keys, vals) in spec.items():
        e = L.add(num, keys, [(f"{nm} Factor", "DECIMAL", 4)], [[v, fac(r)] for v in vals])
        e.apply(lambda t: scale_rows(t, "1.04"))
        if num <= 594:                                                                            # Arizona's numbers in the California book
            for row in e.export.rows:
                row[-1] = fac(rng_az)
        if num == 595:
            L.meta[e.export.code] = {"Jurisdiction": "AZ"}


def b_f10(L: Lab) -> None:
    r = L.rng
    rows = [[f"{(i // 75) + 1:03d}", f"{601 + i:05d}", fac(r, 4, 0.7, 1.6)] for i in range(30000)]
    e = L.add(600, [("Territory", "STRING"), ("Zip Code", "STRING")], [("Territory Zip Factor", "DECIMAL", 4)], rows)
    e.apply(lambda t: scale_rows(t, "1.015"))
    e.export.rows[17432][-1] = q(Decimal(str(e.export.rows[17432][-1])) * Decimal("1.03"), 4)
    del e.export.rows[25000]
    e = L.add(601, [("Census Block", "STRING")], [("Census Factor", "DECIMAL", 4)], [[f"06{i:010d}", fac(r, 4, 0.5, 1.9)] for i in range(8000)])
    e.apply(lambda t: scale_rows(t, "1.01"))
    e.export.rows[3333][-1] = q(Decimal(str(e.export.rows[3333][-1])) * Decimal("0.1"), 4)


def b_e1(L: Lab) -> None:
    r = L.rng
    L.approved_kind = "zip"
    e = L.add(610, [("Territory", "STRING")], [("Base Rate Factor", "DECIMAL", 4)], [[t, fac(r)] for t in terr(10)], sheet="610 Base Rate Messy (FINAL)")
    e.apply(lambda t: scale_rows(t, "1.02"))
    e = L.add(611, [("Territory", "STRING"), ("Class Code", "STRING")], [("Matrix Factor", "DECIMAL", 4)],
              [[t, f"{c:04d}", fac(r)] for t in ("001", "002", "003") for c in range(100, 104)])
    e.apply(lambda t: scale_rows(t, "1.02"))

    def merge(ws: Any) -> None:
        for start in (2, 6, 10):
            ws.merge_cells(start_row=start, start_column=1, end_row=start + 3, end_column=1)
    e.post = merge
    e = L.add(612, [("Territory", "STRING")], [("Struck Factor", "DECIMAL", 4)], [[t, fac(r)] for t in terr(8)], sheet=" 612  Struck Row Factor ")
    e.apply(lambda t: scale_rows(t, "1.02"))

    def strike(ws: Any) -> None:
        for c in ws[3]:
            c.font = Font(strike=True)
    e.post = strike
    e = L.add(613, [("Territory", "STRING")], [("Formula Factor", "DECIMAL", 4)], [[t, fac(r)] for t in terr(8)], sheet="613_Formula_Factor")
    e.apply(lambda t: scale_rows(t, "1.02"))
    e.post = lambda ws: ws.cell(row=3, column=2).__setattr__("value", "=1.0450*1")
    e = L.add(614, [("Territory", "STRING")], [("Error Factor", "DECIMAL", 4)], [[t, fac(r)] for t in terr(8)], sheet="TAB 614 - Error Cell Factor")
    e.apply(lambda t: scale_rows(t, "1.02"))
    e.post = lambda ws: ws.cell(row=4, column=2).__setattr__("value", "#N/A")
    e = L.add(615, [("Territory", "STRING")], [("Hosted Factor", "DECIMAL", 4)], [[t, fac(r)] for t in terr(8)], extra_sheet=True)
    e.apply(lambda t: scale_rows(t, "1.02"))


def b_e1b(L: Lab) -> None:
    r = L.rng
    e = L.add(616, [("Territory", "STRING")], [("Header Only Factor", "DECIMAL", 4)], [[t, fac(r)] for t in terr(10)])
    e.apply(lambda t: scale_rows(t, "1.02"))
    assert e.approved is not None
    e.approved.rows = []                                                                           # headers, no data
    L.add(617, [("Territory", "STRING")], [("Normal Factor", "DECIMAL", 4)], [[t, fac(r)] for t in terr(10)]).apply(lambda t: scale_rows(t, "1.02"))


def b_e2(L: Lab) -> None:
    r = L.rng
    for num, nm in ((620, "Conflicted"), (621, "Unlisted"), (622, "Plain")):
        L.add(num, [("Territory", "STRING")], [(f"{nm} Factor", "DECIMAL", 4)], [[t, fac(r)] for t in terr(10)], jira="update" if num == 622 else None).apply(lambda t: scale_rows(t, "1.02"))
    L.jira_text = "\n".join([
        f"{TICKET}: CA commercial auto rate revision", "",
        "Line of Business: Commercial Auto (CA)", "State: CA",
        "Filing Effective Date: 03/15/2027", "Rate Book Edition: " + NEW_EDITION, "",
        "Tables to update:", "- Table 620 Conflicted Action Table", "- Table 622 Plain Update Table", "",
        "Bypass:", "- Table 620 Conflicted Action Table - DO NOT LOAD", "",
        "Comments:", "Update from product: Filing Effective Date: 04/01/2027", ""])


def b_e3(L: Lab) -> None:
    r = L.rng
    L.add(630, [("Deductible", "INTEGER")], [("Deductible Factor", "DECIMAL", 4)], [[d, fac(r, 4, 0.8, 1.3)] for d in (250, 500, 1000, 2500)]).apply(lambda t: scale_rows(t, "1.02"))
    L.add(631, [("Deductible", "INTEGER")], [("Deductible Factor", "DECIMAL", 4)], [[d, fac(r, 4, 0.8, 1.3)] for d in (250, 500, 1000, 2500)], jira=None, approved=False)


# id -> (builder, tables in world, seed)
BUILDERS: dict[str, tuple[Callable[[Lab], None], int]] = {
    "H1": (b_h1, 50), "H2": (b_h2, 70), "H3": (b_h3, 60), "H4": (b_h4, 55), "H5": (b_h5, 45),
    "F1": (b_f1, 80), "F2": (b_f2, 60), "F3": (b_f3, 90), "F4": (b_f4, 65), "F5": (b_f5, 100),
    "F6": (b_f6, 75), "F7": (b_f7, 85), "F8": (b_f8, 50), "F9": (b_f9, 110), "F10": (b_f10, 120),
    "E1": (b_e1, 40), "E1b": (b_e1b, 40), "E2": (b_e2, 55), "E3": (b_e3, 50),
}
STATE_LOB = {"F9": ("CA", "BOP"), "E2": ("CA", "CA")}


def build_case(case: dict[str, Any], out_root: Path) -> dict[str, Any]:
    cid = case["id"]
    builder, n_tables = BUILDERS[cid]
    state, lob = STATE_LOB.get(cid, ("OR", "BOP"))
    titles = {int(k): v for k, v in case["tables"].items()}
    L = Lab(cid, titles, n_tables, seed=7, state=state, lob=lob)
    builder(L)
    d = out_root / cid / "inputs"
    if d.parent.exists():
        shutil.rmtree(d.parent)
    d.mkdir(parents=True)
    exp_tables, base_tables = L.export_tables(), L.baseline_tables()
    write_export(d / "baseline.xlsx", base_tables, OLD_EDITION, OLD_EFF, rng_seed=2, meta_override=L.meta_for(base_tables))
    write_export(d / "export.xlsx", exp_tables, L.export_edition, L.export_eff, rng_seed=3, meta_override=L.meta_for(exp_tables))
    approved_entries = [e for e in L.entries.values() if e.approved is not None]
    if L.approved_kind == "zip":
        ap = d / "approved.zip"
        write_approved_zip(ap, approved_entries, d / "_tmp")
        shutil.rmtree(d / "_tmp", ignore_errors=True)
    else:
        ap = d / "approved.xlsx"
        write_approved_book(ap, approved_entries)
    (d / "jira.txt").write_text(L.jira(), encoding="utf-8")
    manifest = {
        "case": cid, "tables_in_export": len(exp_tables), "lab_tables": sorted(L.entries), "state": state, "lob": lob,
        "approved_tables": len(approved_entries), "export_cells": sum(len(t.rows) * (t.nk() + len(t.factors)) for t in exp_tables),
        "files": {"approved": ap.name, "export": "export.xlsx", "baseline": "baseline.xlsx", "jira": "jira.txt"},
    }
    (d / "inputs_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"dir": d, "approved": ap, "export": d / "export.xlsx", "baseline": d / "baseline.xlsx", "jira": d / "jira.txt", "manifest": manifest}


def load_cases() -> dict[str, Any]:
    return yaml.safe_load((HERE / "cases.yaml").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append")
    ap.add_argument("--out", default=str(HERE / "out"))
    a = ap.parse_args()
    spec = load_cases()
    for c in spec["cases"]:
        if a.only and c["id"] not in a.only:
            continue
        info = build_case(c, Path(a.out))
        print(f"built {c['id']}: {info['manifest']['tables_in_export']} tables, ~{info['manifest']['export_cells']:,} cells")


if __name__ == "__main__":
    main()
