"""Synthetic fixture generator (spec 17). No real carrier data - everything is invented.

Builds a BOP-like rate book for state OR: a Guidewire-style export with one sheet per table
(50-220+), an approved multi-tab workbook, Jira stories, a baseline export, and mutated scenarios.
"""

from __future__ import annotations

import copy
import random
import shutil
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font, PatternFill

RATE_BOOK = "BOPByPeril_OR_RateBook"
OLD_EDITION, NEW_EDITION = "2025-V1", "2025-V2"
OLD_EFF, NEW_EFF, RENEWAL_EFF = date(2026, 3, 15), date(2027, 3, 15), date(2027, 5, 15)
TICKET = "PC-12345"


def q(v: float | Decimal, scale: int) -> Decimal:
    return Decimal(str(v)).quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)


# ------------------------------------------------------------------ table data
@dataclass
class T:
    code: str
    name: str                     # GW table name (may start with the table number)
    number: int | None
    kind: str                     # single | range | multi | large | named
    keys: list[tuple[str, str]]   # (GW header, GW type)
    factors: list[tuple[str, str, int]]    # (GW header, GW type, scale)
    rows: list[list[Any]]         # key values then factor values
    approved_keys: list[str] = field(default_factory=list)       # approved-side headers
    approved_factors: list[str] = field(default_factory=list)
    approved_sheet: str = ""

    def nk(self) -> int:
        return len(self.keys)

    def copy(self) -> T:
        return copy.deepcopy(self)


def _terr(n: int) -> list[str]:
    return [f"{i:03d}" for i in range(1, n + 1)]


def _named_tables(rng: random.Random) -> list[T]:
    def fac(scale: int, lo: float = 0.6, hi: float = 1.7) -> Decimal:
        return q(rng.uniform(lo, hi), scale)

    t1 = T("BOP_BLD_BaseRate_Ext", "1 BLD Base Rate", 1, "named", [("Territory", "STRING")], [("Base Rate Factor", "DECIMAL", 4)],
           [[t, fac(4)] for t in _terr(12)], ["Terr"], ["Base Rate Factor"], "1 BLD Base Rate")
    t21 = T("BOP_BPP_BaseRate_Ext", "21 BPP Base Rate", 21, "named", [("Territory", "STRING")], [("Base Rate Factor", "DECIMAL", 4)],
            [[t, fac(4)] for t in _terr(12)], ["Terr"], ["Base Rate Factor"], "21 BPP Base Rate")
    classes = [f"{c:04d}" for c in range(40, 60)]
    t42 = T("BOP_GL_ClassCodeFactor_Ext", "42 GL Class Code Factor", 42, "named", [("Class Code", "STRING")], [("Class Factor", "DECIMAL", 4)],
            [[c, fac(4)] for c in classes] + [["", fac(4)]], ["Class"], ["Class Factor"], "42 GL Class Code Factor")
    t48 = T("BOP_EPLI_Coverage_Ext", "48 EPLI Coverage", 48, "named", [("Coverage Limit", "INTEGER")], [("Premium Factor", "DECIMAL", 4)],
            [[lim, fac(4)] for lim in (100000, 250000, 500000, 1000000)], ["Limit"], ["Premium Factor"], "48 EPLI Coverage")
    bands = [(1, 1), (2, 3), (4, 6), (7, 10), (11, 20), (21, None)]
    t121 = T("BOP_BLD_NumStories_Ext", "121 BLD Number of Stories", 121, "named",
             [("Stories From", "INTEGER"), ("Stories To", "INTEGER")], [("Stories Factor", "DECIMAL", 4)],
             [[a, b, q(1.0 + 0.0125 * i, 4) if i != 2 else Decimal("1.0450")] for i, (a, b) in enumerate(bands)],
             ["Stories From", "Stories To"], ["Stories Factor"], "121 BLD Number of Stories")
    terr = T("BOP_TerritoryFactor_Ext", "Territory Factor", None, "named", [("Territory", "STRING")], [("Territory Factor", "DECIMAL", 6)],
             [[t, fac(6, 0.8, 1.4)] for t in _terr(30)], ["Territory"], ["Territory Factor"], "Territory Factor")
    ded = T("BOP_DeductibleFactor_Ext", "Deductible Factor", None, "named", [("Deductible", "INTEGER")], [("Deductible Factor", "DECIMAL", 3)],
            [[d, q(1.2 - 0.05 * i, 3)] for i, d in enumerate((250, 500, 1000, 2500, 5000))], ["Deductible"], ["Deductible Factor"], "Deductible Factor")
    return [t1, t21, t42, t48, t121, terr, ded]


_TOPICS = ["Roof Age", "Protection Class", "Fire Alarm", "Sprinkler", "Wind Hail", "Water Damage", "Burglary", "Theft",
           "Equipment Breakdown", "Spoilage", "Signs", "Outdoor Property", "Money Securities", "Employee Dishonesty",
           "Liquor Liability", "Products Liability", "Hired Non Owned Auto", "Utility Services", "Ordinance Law",
           "Business Income", "Extra Expense", "Valuable Papers", "Accounts Receivable", "Computer Fraud"]
_KEYSETS = [[("Territory", "STRING")], [("Class Code", "STRING")], [("Protection Class", "STRING")], [("Construction", "STRING")]]
_CONSTR = ["FRAME", "JOISTED MASONRY", "NON COMBUSTIBLE", "MASONRY NON COMB", "MODIFIED FIRE RES", "FIRE RESISTIVE"]


def _filler(i: int, kind: str, rng: random.Random) -> T:
    topic = _TOPICS[i % len(_TOPICS)]
    camel = topic.replace(" ", "")
    suffix = "Factor" if kind != "range" else "BandFactor"
    long = i % 3 == 0
    code = f"BOP_{camel}{suffix}_{'ExtensionRateTable_' if long else ''}{i:03d}"
    number = 200 + i if i % 2 == 0 else None
    title = f"{topic} {'Band ' if kind == 'range' else ''}Factor {i:03d}"
    name = f"{number} {title}" if number else title
    scale = rng.choice([3, 4, 4, 5, 6])

    def fac(sc: int = scale) -> Decimal:
        return q(rng.uniform(0.5, 1.9), sc)

    if kind == "single":
        ks = _KEYSETS[i % len(_KEYSETS)]
        n = rng.randint(5, 50)
        if ks[0][0] == "Construction":
            vals = (_CONSTR * 9)[:min(n, 54)]
            suffixes: list[str | int] = [*([""] * 6), *range(1, 100)]
            vals = [f"{v} {k}" if k else str(v) for k, v in zip(vals, suffixes, strict=False)]
        elif ks[0][0] == "Protection Class":
            vals = [str(k) for k in range(1, min(n, 10) + 1)]
        else:
            vals = _terr(n) if ks[0][0] == "Territory" else [f"{c:04d}" for c in range(100, 100 + n)]
        factors: list[tuple[str, str, int]] = [(f"{topic} Factor", "DECIMAL", scale)]
        rows: list[list[Any]] = [[v, fac()] for v in vals]
        if i % 10 == 1:
            factors = [(f"{topic} Minimum Value", "INTEGER", 0)]
            rows = [[v, rng.randint(25, 500)] for v in vals]
        elif i % 17 == 5:
            factors = [(f"{topic} Applies Value", "BOOLEAN", 0)]
            rows = [[v, rng.choice(["Y", "N"])] for v in vals]
        if i % 7 == 3 and rows and factors[0][1] == "DECIMAL":
            rows.append(["", fac()])         # wildcard row
        return T(code, name, number, kind, ks, factors, rows, [ks[0][0]], [factors[0][0]], name)
    if kind == "range":
        unit = rng.choice(["Limit", "Sq Ft", "Deductible", "Stories"])
        nb = rng.randint(5, 40)
        start, rows = 0, []
        for b in range(nb):
            width = rng.randint(1, 50) * (100 if unit != "Stories" else 1)
            lo, hi = start, start + width - 1
            rows.append([lo, hi if b < nb - 1 else "", q(0.8 + 0.01 * b, scale)])
            start = hi + 1
        return T(code, name, number, kind, [(f"{unit} From", "INTEGER"), (f"{unit} To", "INTEGER")],
                 [(f"{topic} Factor", "DECIMAL", scale)], rows, [f"{unit} From", f"{unit} To"], [f"{topic} Factor"], name)
    # multi / large
    if kind == "large":
        nt, nc, nco = rng.randint(30, 60), rng.randint(20, 40), rng.randint(6, 10)
        ks = [("Territory", "STRING"), ("Class Code", "STRING"), ("Construction", "STRING")]
        rows = [[t, f"{c:04d}", _CONSTR[k % 6] if nco <= 6 else f"{_CONSTR[k % 6]} {k}", fac()]
                for t in _terr(nt) for c in range(100, 100 + nc) for k in range(nco)]
    else:
        nt = rng.randint(5, 30)
        nc = max(5, min(100, rng.randint(100, 2500) // nt))
        ks = [("Territory", "STRING"), ("Class Code", "STRING")]
        rows = [[t, f"{c:04d}", fac()] for t in _terr(nt) for c in range(100, 100 + nc)]
    return T(code, name, number, kind, ks, [(f"{topic} Factor", "DECIMAL", scale)], rows,
             [k[0] for k in ks], [f"{topic} Factor"], name)


def build_world(n_tables: int = 120, seed: int = 7) -> list[T]:
    """The *prior* state of the rate book (baseline edition)."""
    rng = random.Random(seed)
    tables = _named_tables(rng)
    n_large = max(1, n_tables // 60)
    i = 0
    while len(tables) + 0 < n_tables - 1:        # reserve one slot for the new table 10
        i += 1
        slot = i % 20
        if slot in (18, 19) and sum(1 for t in tables if t.kind == "large") < n_large:
            kind = "large"
        elif slot < 8 or slot in (18, 19):
            kind = "single"
        elif slot < 13:
            kind = "range"
        else:
            kind = "multi"
        tables.append(_filler(i, kind, rng))
    return tables


def apply_change(old: list[T]) -> list[T]:
    """The *intended* new state: changed named tables + brand new table 10."""
    new = [t.copy() for t in old]
    by = {t.code: t for t in new}

    def bump(t: T, factor: str, col: int = -1) -> None:
        sc = t.factors[0][2]
        for r in t.rows:
            r[col] = q(Decimal(str(r[col])) * Decimal(factor), sc)

    bump(by["BOP_BLD_BaseRate_Ext"], "1.05")
    bump(by["BOP_BPP_BaseRate_Ext"], "1.03")
    bump(by["BOP_GL_ClassCodeFactor_Ext"], "1.02")
    by["BOP_GL_ClassCodeFactor_Ext"].rows.insert(0, ["0099", Decimal("1.1100")])
    bump(by["BOP_TerritoryFactor_Ext"], "1.011")
    bump(by["BOP_DeductibleFactor_Ext"], "0.99")
    t121 = by["BOP_BLD_NumStories_Ext"]
    for r in t121.rows:
        r[-1] = q(Decimal(str(r[-1])) + Decimal("0.0100"), 4)
    t121.rows[2][-1] = Decimal("1.0450")
    new.append(T("BOP_VandalismExclusion_Ext", "10 Vandalism Exclusion", 10, "named", [("Vandalism Excluded", "BOOLEAN")],
                 [("Credit Factor", "DECIMAL", 3)], [["Y", Decimal("0.900")], ["N", Decimal("1.000")]],
                 ["Vandalism Excluded"], ["Credit Factor"], "10 Vandalism Exclusion"))
    return new


APPROVED_CODES = ["BOP_BLD_BaseRate_Ext", "BOP_BPP_BaseRate_Ext", "BOP_GL_ClassCodeFactor_Ext", "BOP_EPLI_Coverage_Ext",
                  "BOP_BLD_NumStories_Ext", "BOP_VandalismExclusion_Ext", "BOP_TerritoryFactor_Ext", "BOP_DeductibleFactor_Ext"]

JIRA_TXT = """{ticket}: Oregon BOP rate revision

LOB: BOP
State: OR
Filing Effective Date: 03/15/2027
Renewal Effective Date: 05/15/2027
Rate Book Edition: {edition}
Applies to: New Business and Renewal transactions

Tables to update:
- Table 1 BLD Base Rate
- Table 21 BPP Base Rate
- Table 42 GL Class Code Factor
- Table 121 BLD Number of Stories
- Territory Factor
- Deductible Factor

New tables:
- Table 10 Vandalism Exclusion

Bypass:
- ~~Table 48~~ DO NOT LOAD (EPLI Coverage)

Comments:
Actuarial approved the attached workbook. EPLI is not part of this filing.
"""

JIRA_MD = """# {ticket}: Oregon BOP rate revision

**LOB:** BOP
**State:** OR
**Filing Effective Date:** 03/15/2027
**Renewal Effective Date:** 05/15/2027
**Rate Book Edition:** {edition}
**Applies to:** New Business and Renewal transactions

## Tables to update:
* Table 1 BLD Base Rate
* Table 21 BPP Base Rate
* Table 42 GL Class Code Factor
* Table 121 BLD Number of Stories
* Territory Factor
* Deductible Factor

## New tables:
* Table 10 Vandalism Exclusion

## Bypass:
* ~~Table 48~~ DO NOT LOAD (EPLI Coverage)
"""

JIRA_HTML = """<html><body><h1>{ticket}: Oregon BOP rate revision</h1>
<p>LOB: BOP</p><p>State: OR</p><p>Filing Effective Date: 03/15/2027</p>
<p>Renewal Effective Date: 05/15/2027</p><p>Rate Book Edition: {edition}</p>
<p>Applies to: New Business and Renewal transactions</p>
<h2>Tables to update:</h2>
<ul><li>Table 1 BLD Base Rate</li><li>Table 21 BPP Base Rate</li><li>Table 42 GL Class Code Factor</li>
<li>Table 121 BLD Number of Stories</li><li>Territory Factor</li><li>Deductible Factor</li></ul>
<h2>New tables:</h2><ul><li>Table 10 Vandalism Exclusion</li></ul>
<h2>Bypass:</h2><ul><li><del>Table 48</del> DO NOT LOAD (EPLI Coverage)</li></ul>
<script>alert('x')</script></body></html>
"""


# ------------------------------------------------------------------ writers
def _sheet_names(tables: list[T]) -> dict[str, str]:
    """Excel-legal, <=31 chars, unique (suffix on collision) - mimics Guidewire truncation."""
    used: dict[str, str] = {}
    out: dict[str, str] = {}
    for t in tables:
        base = t.code[:31]
        name, k = base, 1
        while name.lower() in used:
            sfx = f"~{k}"
            name = base[: 31 - len(sfx)] + sfx
            k += 1
        used[name.lower()] = t.code
        out[t.code] = name
    return out


def write_export(path: Path, tables: list[T], edition: str, eff: date, rng_seed: int = 1,
                 book_code: str = RATE_BOOK, meta_override: dict[str, dict[str, str]] | None = None) -> None:
    rng = random.Random(rng_seed)
    wb = openpyxl.Workbook(write_only=True)
    names = _sheet_names(tables)
    for ti, t in enumerate(tables):
        ws = wb.create_sheet(names[t.code])
        md = {"Rate Book Code": book_code, "Edition": edition, "Table Code": t.code, "Table Name": t.name,
              "Effective Date": eff, "Jurisdiction": "OR", "Line of Business": "BOP"}
        if meta_override and t.code in meta_override:
            md.update(meta_override[t.code])
        for label, val in md.items():
            if isinstance(val, date):
                if ti % 2 == 0:
                    val = val.strftime("%m/%d/%Y")
                else:
                    c = WriteOnlyCell(ws, value=datetime(val.year, val.month, val.day))
                    c.number_format = "mm/dd/yyyy"
                    ws.append([label, c])
                    continue
            ws.append([label, val])
        ws.append([])
        ws.append([k[1] for k in t.keys] + [(f"{f[1]}({10},{f[2]})" if f[1] == "DECIMAL" else f[1]) for f in t.factors])
        ws.append([k[0] for k in t.keys] + [f[0] for f in t.factors])
        rows = list(t.rows)
        rng.shuffle(rows)
        nk = t.nk()
        for r in rows:
            out: list[Any] = []
            for j, v in enumerate(r):
                if isinstance(v, Decimal):
                    out.append(float(v))
                elif v == "" or v is None:
                    out.append(None)
                elif j < nk and t.keys[j][1] == "STRING" and False:
                    out.append(v)
                else:
                    out.append(v)
            ws.append(out)
    wb.save(path)


def write_approved(path: Path, tables: list[T], highlight: bool = True, strike_row: tuple[str, int] | None = None,
                   number_format_codes: bool = False, formula_cell: tuple[str, int, int] | None = None) -> None:
    wb = openpyxl.Workbook()
    first = wb.active
    assert first is not None
    wb.remove(first)
    yellow = PatternFill("solid", fgColor="FFFF00")
    for t in tables:
        ws = wb.create_sheet(t.approved_sheet[:31])
        ws.append(list(t.approved_keys) + list(t.approved_factors))
        for ri, r in enumerate(sorted(t.rows, key=lambda x: tuple(str(c) for c in x[: t.nk()])), start=2):
            vals: list[Any] = []
            for j, v in enumerate(r):
                if isinstance(v, Decimal):
                    vals.append(float(v))
                elif j < t.nk() and t.keys[j][1] == "STRING" and number_format_codes and isinstance(v, str) and v.isdigit():
                    vals.append(int(v))
                elif j < t.nk() and t.keys[j][0] in ("Class Code",) and v == "":
                    vals.append("*")
                else:
                    vals.append(v)
            ws.append(vals)
            if number_format_codes and t.keys[0][1] == "STRING":
                ws.cell(row=ri, column=1).number_format = "0" * max(len(str(r[0])), 1)
            if highlight and ri % 5 == 0:
                for c in ws[ri]:
                    c.fill = yellow
            if strike_row and strike_row[0] == t.code and ri == strike_row[1]:
                for c in ws[ri]:
                    c.font = Font(strike=True)
        if formula_cell and formula_cell[0] == t.code:
            ws.cell(row=formula_cell[1], column=formula_cell[2]).value = "=1.0450*1"
    wb.save(path)


def _write_jira(dirp: Path, edition: str = NEW_EDITION, text: str | None = None) -> Path:
    p = dirp / "jira.txt"
    p.write_text(text if text is not None else JIRA_TXT.format(ticket=TICKET, edition=edition), encoding="utf-8")
    (dirp / "jira.md").write_text(JIRA_MD.format(ticket=TICKET, edition=edition), encoding="utf-8")
    (dirp / "jira.html").write_text(JIRA_HTML.format(ticket=TICKET, edition=edition), encoding="utf-8")
    return p


# ------------------------------------------------------------------ scenarios
@dataclass
class Ctx:
    old: list[T]
    new: list[T]
    export: list[T]
    approved: list[T]
    jira: str | None = None
    edition: str = NEW_EDITION
    eff: date = NEW_EFF
    meta_override: dict[str, dict[str, str]] = field(default_factory=dict)
    use_baseline_flag: bool = True
    seed_snapshot: bool = False
    profile: str = "default"
    approved_kind: str = "xlsx"          # xlsx | zip | xlsm | folder
    write_kwargs: dict[str, Any] = field(default_factory=dict)
    post: Callable[[Path], None] | None = None   # post-process approved path (unsafe zip etc.)
    expected_verdict: str = "PASS"
    expected_rules: set[str] = field(default_factory=set)
    expected_exit: int = 0
    tables: int | None = None

    def exp(self, code: str) -> T:
        return next(t for t in self.export if t.code == code)

    def appr(self, code: str) -> T:
        return next(t for t in self.approved if t.code == code)


@dataclass
class Scenario:
    name: str
    dir: Path
    approved: Path
    jira: Path
    export: Path
    baseline: Path | None
    use_baseline_flag: bool
    seed_snapshot: bool
    expected_verdict: str
    expected_rules: set[str]
    expected_exit: int
    profile: str = "default"

    def cli_args(self, out: Path) -> list[str]:
        a = ["run", "--approved", str(self.approved), "--jira", str(self.jira), "--export", str(self.export), "--out", str(out)]
        if self.use_baseline_flag and self.baseline:
            a += ["--baseline", str(self.baseline)]
        return a


def _m_factor_mismatch(c: Ctx) -> None:
    c.exp("BOP_BLD_NumStories_Ext").rows[2][-1] = Decimal("1.0250")
    c.expected_verdict, c.expected_rules, c.expected_exit = "FAIL", {"VALUE_MISMATCH"}, 1


def _m_oom(c: Ctx) -> None:
    c.exp("BOP_BLD_NumStories_Ext").rows[2][-1] = Decimal("10.4500")
    c.expected_verdict, c.expected_rules, c.expected_exit = "FAIL", {"ORDER_OF_MAGNITUDE"}, 1


def _m_rounded(c: Ctx) -> None:
    t = c.appr("BOP_TerritoryFactor_Ext")          # approved keeps 6 d.p.; export column scale is 4
    et = c.exp("BOP_TerritoryFactor_Ext")
    et.factors = [(et.factors[0][0], "DECIMAL", 4)]
    for ra, re_ in zip(sorted(t.rows), sorted(et.rows), strict=True):
        ra[-1] = Decimal(ra[-1]) + Decimal("0.000012")
        re_[-1] = q(ra[-1], 4)
    c.expected_verdict, c.expected_rules = "PASS", {"MATCH_ROUNDED"}


def _m_trunc(c: Ctx) -> None:
    t = c.appr("BOP_DeductibleFactor_Ext")
    et = c.exp("BOP_DeductibleFactor_Ext")
    et.factors = [(et.factors[0][0], "DECIMAL", 4)]
    for ra, re_ in zip(sorted(t.rows), sorted(et.rows), strict=True):
        ra[-1] = Decimal("0.0123456")
        re_[-1] = Decimal("0.0123")
    c.expected_verdict, c.expected_rules, c.expected_exit = "PASS_WITH_WARNINGS", {"PRECISION_TRUNCATION"}, 5


def _m_missing_row(c: Ctx) -> None:
    c.exp("BOP_BLD_BaseRate_Ext").rows.pop(3)
    c.expected_verdict, c.expected_rules, c.expected_exit = "FAIL", {"MISSING_ROW_IN_EXPORT"}, 1


def _m_extra_row(c: Ctx) -> None:
    c.exp("BOP_BPP_BaseRate_Ext").rows.append(["999", Decimal("1.0000")])
    c.expected_verdict, c.expected_rules, c.expected_exit = "FAIL", {"EXTRA_ROW_IN_EXPORT"}, 1


def _m_small(c: Ctx) -> None:
    t = c.appr("BOP_GL_ClassCodeFactor_Ext")
    t.rows = t.rows[: max(3, len(t.rows) // 3)]
    c.expected_verdict, c.expected_rules, c.expected_exit = "FAIL", {"APPROVED_TABLE_SUSPICIOUSLY_SMALL", "EXTRA_ROW_IN_EXPORT"}, 1


def _first_filler(c: Ctx) -> T:
    return next(t for t in c.export if t.kind == "single" and t.factors[0][1] == "DECIMAL" and t.code not in APPROVED_CODES)


def _m_snap_undeclared(c: Ctx) -> None:
    c.use_baseline_flag, c.seed_snapshot, c.profile = False, True, "snapshots"
    t = _first_filler(c)
    t.rows[1][-1] = t.rows[1][-1] + Decimal("0.1")
    c.expected_verdict, c.expected_rules, c.expected_exit = "FAIL", {"UNDECLARED_CHANGE"}, 1


def _m_snap_first(c: Ctx) -> None:
    c.use_baseline_flag, c.profile = False, "snapshots_strict"
    c.expected_verdict, c.expected_rules, c.expected_exit = "PASS_WITH_LIMITATIONS", {"NOT_EVALUATED_NO_BASELINE"}, 5


def _m_perf(c: Ctx) -> None:
    c.tables = 220


def _m_trunc_names(c: Ctx) -> None:
    for i, t in enumerate(c.export):
        if t.kind == "single" and i % 2:
            t.code = t.code + "_VeryLongSuffixToForceTruncationOfSheetName"   # sheets collide at 31 chars
    for i, t in enumerate(c.old):
        if t.kind == "single" and i % 2:
            t.code = t.code + "_VeryLongSuffixToForceTruncationOfSheetName"


def _m_blank_factor(c: Ctx) -> None:
    t = c.exp("BOP_GL_ClassCodeFactor_Ext")
    for r in t.rows:
        if r[0] == "":
            r[-1] = None
    c.expected_verdict, c.expected_rules, c.expected_exit = "FAIL", {"BLANK_FACTOR"}, 1


def _m_off_by_one(c: Ctx) -> None:
    t = c.exp("BOP_BLD_NumStories_Ext")
    t.rows[2][1], t.rows[3][0] = 7, 8          # [4,6]->[4,7], [7,10]->[8,10]
    c.expected_verdict, c.expected_rules, c.expected_exit = "FAIL", {"OFF_BY_ONE_BOUNDARY"}, 1


def _m_range_gap(c: Ctx) -> None:
    t = c.exp("BOP_BLD_NumStories_Ext")
    t.rows[3][0] = 8                           # gap between 6 and 8
    c.expected_verdict, c.expected_rules, c.expected_exit = "FAIL", {"RANGE_GAP"}, 1


def _m_dup(c: Ctx) -> None:
    t = c.exp("BOP_BLD_BaseRate_Ext")
    dup = list(t.rows[0])
    dup[-1] = dup[-1] + Decimal("0.0100")           # same key, different value => ambiguous => BLOCKER
    t.rows.append(dup)
    c.expected_verdict, c.expected_rules, c.expected_exit = "BLOCKED", {"DUPLICATE_KEY"}, 2


def _m_formula(c: Ctx) -> None:
    c.write_kwargs["formula_cell"] = ("BOP_BLD_NumStories_Ext", 4, 3)
    c.expected_verdict, c.expected_rules, c.expected_exit = "BLOCKED", {"FORMULA_NO_VALUE"}, 2


def _m_bypass(c: Ctx) -> None:
    c.exp("BOP_EPLI_Coverage_Ext").rows[0][-1] = Decimal("1.5000")
    c.expected_verdict, c.expected_rules, c.expected_exit = "FAIL", {"BYPASS_VIOLATION"}, 1


def _m_undeclared(c: Ctx) -> None:
    t = _first_filler(c)
    t.rows[0][-1] = t.rows[0][-1] + Decimal("0.1")
    c.expected_verdict, c.expected_rules, c.expected_exit = "FAIL", {"UNDECLARED_CHANGE"}, 1


def _m_missing_update_resolved(c: Ctx) -> None:
    # Approved says table 21 is unchanged vs the prior edition and the export agrees.
    old21 = next(t for t in c.old if t.code == "BOP_BPP_BaseRate_Ext")
    for lst in (c.export, c.approved):
        next(t for t in lst if t.code == "BOP_BPP_BaseRate_Ext").rows = copy.deepcopy(old21.rows)
    c.expected_verdict, c.expected_rules = "PASS", {"APPROVED_ROW_UNCHANGED"}


def _m_eff_date(c: Ctx) -> None:
    c.eff = date(2027, 4, 1)
    c.expected_verdict, c.expected_rules, c.expected_exit = "FAIL", {"EFFECTIVE_DATE_MISMATCH"}, 1


def _m_no_baseline(c: Ctx) -> None:
    # D-024: by default a run with no other issues PASSes without a baseline (the report says the scope check was not performed).
    c.use_baseline_flag = False
    c.expected_verdict, c.expected_rules, c.expected_exit = "PASS", set(), 0


def _m_no_baseline_strict(c: Ctx) -> None:
    c.use_baseline_flag, c.profile = False, "strict_baseline"
    c.expected_verdict, c.expected_rules, c.expected_exit = "PASS_WITH_LIMITATIONS", {"NOT_EVALUATED_NO_BASELINE"}, 5


def _m_out_of_scope_dated(c: Ctx) -> None:
    # Some tables outside the approved file and Jira keep their old stamps; the rest carry this filing's date and edition.
    fillers = [t for t in c.export if t.code not in APPROVED_CODES]
    for t in fillers[: max(1, len(fillers) * 2 // 5)]:
        c.meta_override[t.code] = {"Edition": OLD_EDITION, "Effective Date": OLD_EFF.strftime("%m/%d/%Y")}
    c.expected_verdict, c.expected_rules, c.expected_exit = "NEEDS_REVIEW", {"OUT_OF_SCOPE_TABLE_DATED"}, 2


def _m_unmappable(c: Ctx) -> None:
    t = c.appr("BOP_DeductibleFactor_Ext")
    t.approved_sheet = "Ded Schedule Adj"
    t2 = c.appr("BOP_TerritoryFactor_Ext")
    t2.approved_sheet = "Terr Rel Adj"
    c.expected_verdict, c.expected_rules, c.expected_exit = "BLOCKED", {"NEEDS_MAPPING"}, 2


def _m_leading_zero(c: Ctx) -> None:
    c.write_kwargs["number_format_codes"] = True
    c.expected_verdict = "PASS"


def _m_zip(c: Ctx) -> None:
    c.approved_kind = "zip"


def _m_xlsm(c: Ctx) -> None:
    c.approved_kind = "xlsm"
    c.expected_rules = {"VBA_PRESENT"}


def _m_strike(c: Ctx) -> None:
    c.write_kwargs["strike_row"] = ("BOP_BLD_BaseRate_Ext", 3)
    c.expected_verdict, c.expected_rules, c.expected_exit = "NEEDS_REVIEW", {"STRIKETHROUGH_ROW"}, 2


def _post_traversal(p: Path) -> None:
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("../evil.xlsx", b"PK\x03\x04")
        zf.writestr("ok.xlsx", b"PK\x03\x04")


def _m_traversal(c: Ctx) -> None:
    c.approved_kind, c.post = "zip", _post_traversal
    c.expected_verdict, c.expected_exit = "BLOCKED", 3


def _post_encrypted(p: Path) -> None:
    p.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512)


def _m_encrypted(c: Ctx) -> None:
    c.post = _post_encrypted
    c.expected_verdict, c.expected_exit = "BLOCKED", 3


SCENARIOS: dict[str, Callable[[Ctx], None]] = {
    "clean_pass": lambda c: None,
    "factor_mismatch": _m_factor_mismatch,
    "order_of_magnitude": _m_oom,
    "rounded_match_only": _m_rounded,
    "precision_truncation": _m_trunc,
    "missing_row": _m_missing_row,
    "extra_row_full_table": _m_extra_row,
    "approved_table_suspiciously_small": _m_small,
    "snapshot_baseline_undeclared_change": _m_snap_undeclared,
    "snapshot_first_run_limitations": _m_snap_first,
    "no_baseline_strict_limitations": _m_no_baseline_strict,
    "out_of_scope_table_dated": _m_out_of_scope_dated,
    "export_220_tables_perf": _m_perf,
    "truncated_sheet_names": _m_trunc_names,
    "wildcard_to_blank": _m_blank_factor,
    "off_by_one_range": _m_off_by_one,
    "range_gap": _m_range_gap,
    "duplicate_key": _m_dup,
    "formula_without_cache": _m_formula,
    "bypass_violation": _m_bypass,
    "undeclared_change": _m_undeclared,
    "missing_update_resolved_by_full_table": _m_missing_update_resolved,
    "effective_date_mismatch": _m_eff_date,
    "no_baseline_default_pass": _m_no_baseline,
    "unmappable_table_blocked": _m_unmappable,
    "leading_zero_class_code": _m_leading_zero,
    "multi_file_zip_approved": _m_zip,
    "unsafe_zip_traversal": _m_traversal,
    "encrypted_file": _m_encrypted,
    "xlsm_with_macro_warning": _m_xlsm,
    "strikethrough_row": _m_strike,
}


def build_scenario(name: str, out_dir: Path, tables: int = 120, seed: int = 7) -> Scenario:
    out_dir = Path(out_dir)
    d = out_dir / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    mut = SCENARIOS[name]
    probe = Ctx([], [], [], [])
    if name == "export_220_tables_perf":
        tables = 220
    old = build_world(tables, seed)
    new = apply_change(old)
    approved = [t.copy() for t in new if t.code in APPROVED_CODES]
    epli_old = next(t for t in old if t.code == "BOP_EPLI_Coverage_Ext")
    for i, t in enumerate(approved):
        if t.code == "BOP_EPLI_Coverage_Ext":
            approved[i] = epli_old.copy()
    ctx = Ctx(old, new, [t.copy() for t in new], approved)
    _ = probe
    mut(ctx)
    export_p = d / "export.xlsx"
    baseline_p = d / "baseline.xlsx"
    write_export(baseline_p, old, OLD_EDITION, OLD_EFF, rng_seed=2)
    write_export(export_p, ctx.export, ctx.edition, ctx.eff, rng_seed=3, meta_override=ctx.meta_override)
    ap = d / "approved.xlsx"
    write_approved(ap, ctx.approved, **ctx.write_kwargs)
    approved_path = ap
    if ctx.approved_kind == "zip":
        zp = d / "approved.zip"
        # one workbook per approved table
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
            for t in ctx.approved:
                tp = d / f"_{t.code}.xlsx"
                write_approved(tp, [t], **{k: v for k, v in ctx.write_kwargs.items() if k != "formula_cell"})
                zf.write(tp, f"{t.approved_sheet}.xlsx")
                tp.unlink()
        ap.unlink()
        approved_path = zp
        if ctx.post:
            ctx.post(zp)
    elif ctx.approved_kind == "xlsm":
        xp = d / "approved.xlsm"
        with zipfile.ZipFile(ap) as zin, zipfile.ZipFile(xp, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                zout.writestr(item, zin.read(item.filename))
            zout.writestr("xl/vbaProject.bin", b"\x00\x01fake-macro-bytes")
        ap.unlink()
        approved_path = xp
    elif ctx.post:
        ctx.post(ap)
    jira = _write_jira(d, ctx.edition if False else NEW_EDITION, ctx.jira)
    return Scenario(name, d, approved_path, jira, export_p, baseline_p, ctx.use_baseline_flag, ctx.seed_snapshot,
                    ctx.expected_verdict, ctx.expected_rules, ctx.expected_exit, ctx.profile)


def build_drift_pair(out_dir: Path, tables: int = 50, seed: int = 7) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    old = build_world(tables, seed)
    right = [t.copy() for t in old]
    right[0].rows[0][-1] = right[0].rows[0][-1] + Decimal("0.01")
    right[1].rows.pop()
    right.pop()                                   # only-left table
    right.append(copy.deepcopy(old[2]).__class__(**{**old[2].__dict__, "code": "BOP_OnlyRight_Ext", "name": "Only Right", "number": None}))
    left_p, right_p = out_dir / "left_SAASQA.xlsx", out_dir / "right_GWUpdate.xlsx"
    write_export(left_p, old, OLD_EDITION, OLD_EFF, 2)
    write_export(right_p, right, OLD_EDITION, OLD_EFF, 3)
    return left_p, right_p


def write_sample(out_dir: Path, tables: int = 60) -> list[Scenario]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    return [build_scenario(n, out_dir, tables=tables) for n in ("clean_pass", "factor_mismatch")]
