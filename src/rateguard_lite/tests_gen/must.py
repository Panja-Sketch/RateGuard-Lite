"""Risk-based "Must Test" list (D-026).

RateGuard-Lite already compares every table cell, so these QA tests do NOT re-check factor values row by row.
They cover what a file comparison cannot prove (which rate book a date picks, whether a table is really used
in rating, band edges, wildcard rows, new keys, out-of-scope controls, fixes) and are ranked by a fixed score.

Score (documented in docs/FINDING_CATALOG.md):
    40 fix retest (C10) + 25 date / rate book selection (C1, C2, C3) + 20 new key or new table (C8)
    + 15 range edge in a changed band (C6) + 10 * min(1, |change| / 10%) + 10 wildcard / default row (C7)
    + 5 when the story names this table or feature.
Control tests that carry no table-specific risk (C4, C9, C11) use a fixed base of 12 (+5 when the story names them).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from ..config import aux
from ..engines.common import build_index, key_order, key_text, row_key, to_decimal_safe
from ..model.core import ColumnRole, ColumnType, RateTable, Row
from ..plain import pretty_cell, range_stem
from .generate import _Lookup, _mid, _tid

if TYPE_CHECKING:
    from ..pipeline import RunResult
    from .generate import TestSuite

HARD_CAP = 20
CONTROL_BASE = 15          # control tests: 15 + 5 when the story mentions the topic = 20, the Must Test threshold
MIN_SCORE = 20.0
FIX_RULES = {"VALUE_MISMATCH", "ORDER_OF_MAGNITUDE", "TRANSPOSITION", "SIGN_FLIP", "MISSING_ROW_IN_EXPORT", "WILDCARD_MISMATCH",
             "BLANK_FACTOR", "OFF_BY_ONE_BOUNDARY", "PRECISION_TRUNCATION", "EXTRA_ROW_IN_EXPORT"}
FEES_RE = re.compile(r"\b(fee|fees|surcharge|surcharges|cancel\w*)\b", re.I)
CHANGE_RE = re.compile(r"\b(endorse\w*|policy[- ]changes?|mid-?term|premium[- ]bearing|change transactions?)\b", re.I)
OTHER_RE = re.compile(r"\b(other|another|all|multi-?|every)[- ]+(states?|lines?|lob|jurisdictions?)\b|\bcountrywide\b", re.I)
FULL_LIST_LINE = "Full candidate list: test_scenarios.xlsx > All Candidates"


@dataclass
class Cand:
    id: str
    cats: list[str]
    score: float
    table_id: str
    table: str
    what: str
    why: str
    setup: str
    expect: str
    source: str
    named: bool = False
    parts: dict[str, float] = field(default_factory=dict)
    state: str = "candidate"          # candidate | selected | combined | cut
    note: str = ""
    excluded: str = ""                # why the candidate stays out of the Must Test list (All Candidates only)

    @property
    def cat(self) -> str:
        return "+".join(self.cats)


def _fmt_date(iso: str | None, fallback: str) -> str:
    if not iso:
        return fallback
    d = date.fromisoformat(iso)
    return f"{d.month}/{d.day}/{d.year}"


def _shift(iso: str | None, days: int, fallback: str) -> str:
    if not iso:
        return f"{fallback} {days:+d} day" if days else fallback
    d = date.fromisoformat(iso) + timedelta(days=days)
    return f"{d.month}/{d.day}/{d.year}"


def _num(v: Decimal) -> str:
    return format(v.normalize(), "f")


class _TableInfo:
    """Everything the candidate builders need about one approved table."""

    def __init__(self, res: RunResult, pair: Any, fmap: dict[str, str]) -> None:
        self.pair = pair
        self.a: RateTable = pair.approved
        self.fmap = fmap
        self.lk = _Lookup(self.a)
        self.order = key_order(self.a)
        self.a_rows = {row_key(r, self.order): r for r in self.a.rows}
        self.d = res.deltas.get(pair.id)
        self.named = bool(pair.jira)
        self.has_baseline = self.d is not None and pair.export is not None
        self.keys: list[Any] = []
        self.added: list[Any] = []
        self.pct: dict[Any, float] = {}
        if self.has_baseline:
            assert self.d is not None
            self.added = sorted((k for k in self.d.added if k in self.a_rows), key=str)
            self.keys = [k for k in (self.d.changed + self.d.added) if k in self.a_rows]
        else:
            self.keys = list(self.a_rows)
        if self.d is not None and pair.baseline is not None and pair.baseline.rows:
            bidx = build_index(pair.baseline).rows
            for k in self.keys:
                old, new = bidx.get(k), self.a_rows[k]
                try:
                    f = next(iter(new.factors))
                    o, n = (to_decimal_safe(old.factors[f].value) if old else None), to_decimal_safe(new.factors[f].value)
                    if old is not None and o and n is not None and o != 0:
                        self.pct[k] = abs(float((n - o) / o))
                except (KeyError, StopIteration, ZeroDivisionError):
                    pass
        self.is_range = self.lk.mi is not None and self.lk.ma is not None
        self.header = ", ".join(c.source_header for c in self.a.factor_columns()) or "factor"

    # -- wording helpers
    def label(self, i: int) -> str:
        c = self.a.key_columns()[i]
        if c.canonical_name in self.fmap:
            return self.fmap[c.canonical_name]
        rs = range_stem(c.source_header or "")
        return rs[0] if rs else (c.source_header or c.canonical_name)

    def inputs(self, row: Row, value: Decimal | None = None) -> str:
        parts: list[str] = []
        for i, c in enumerate(self.a.key_columns()):
            if c.role == ColumnRole.RANGE_MAX:
                continue
            cell = row.key[i]
            if c.role == ColumnRole.RANGE_MIN and value is not None:
                v = _num(value)
            elif cell.kind == "WILDCARD":
                v = "a value that is not listed"
            else:
                v = cell.value if cell.value is not None else "blank"
            parts.append(f"{self.label(i)} = {v}")
        return ", ".join(parts) or "any inputs for this table"

    def factor_text(self, row: Row | None) -> str:
        if row is None:
            return "the band that matches"
        vals = [f"{self.a.factor_columns()[j].source_header} {row.factors[n].raw}" for j, n in enumerate(self.lk.fname) if n in row.factors]
        return " and ".join(vals) or "the approved value"

    def factor_value(self, row: Row | None) -> str:
        if row is None:
            return "the factor of the band that matches"
        cols = self.a.factor_columns()
        vals = [(cols[j].source_header, row.factors[n].raw) for j, n in enumerate(self.lk.fname) if n in row.factors]
        if len(vals) == 1:
            return f"{vals[0][1]}"
        return " and ".join(f"{h} {v}" for h, v in vals) or "the approved value"

    def expect(self, row: Row | None, pct: float | None) -> str:
        change = f" It changed by {pct * 100:.1f}% in this filing." if pct else ""
        return f"In the rating worksheet, the {self.header} for {self.pair.display} should be {self.factor_value(row)}.{change}"

    def source(self, row: Row) -> str:
        coord = row.key[0].coord if row.key else ""
        return f"Approved file {self.a.source.file}, tab {self.a.source.sheet}" + (f", cell {coord}" if coord else "")

    def edge_value(self, row: Row) -> Decimal | None:
        assert self.lk.mi is not None
        return to_decimal_safe(row.key[self.lk.mi].value) if row.key[self.lk.mi].kind == "VALUE" else None

    def mid_value(self, row: Row) -> Decimal | None:
        assert self.lk.mi is not None and self.lk.ma is not None
        lo = self.edge_value(row)
        hi = to_decimal_safe(row.key[self.lk.ma].value) if row.key[self.lk.ma].kind == "VALUE" else None
        if lo is not None and hi is not None:
            m = _mid(lo, hi)
            return m.quantize(Decimal(1)) if self.a.key_columns()[self.lk.mi].type == ColumnType.INTEGER else m
        return lo


def _pct_term(pct: float | None) -> float:
    return 10 * min(1.0, (pct or 0.0) / 0.10)


def _table_candidates(res: RunResult, ti: _TableInfo) -> list[Cand]:
    out: list[Cand] = []
    p, disp = ti.pair, ti.pair.display
    sk = sorted(ti.keys, key=lambda k: (-ti.pct.get(k, 0.0), str(k)))
    named5 = 5.0 if ti.named else 0.0

    def mk(cats: list[str], variant: str, k: Any, row: Row, base: dict[str, float], what: str, why: str, inputs: str, exp: str) -> None:
        score = sum(base.values())
        out.append(Cand(id=_tid("MT", cats[0], p.id, variant, key_text(ti.a, k)), cats=cats, score=score, table_id=p.id, table=disp, what=what, why=why,
                        setup=inputs, expect=exp, source=ti.source(row), named=ti.named, parts=base))

    if not sk:
        return out
    # C5: the changed table is really used in rating
    k5 = sk[0] if ti.pct else sk[len(sk) // 2]
    r5 = ti.a_rows[k5]
    v5 = ti.mid_value(r5) if ti.is_range else None
    exp_row = ti.lk.factor({i: r5.key[i].value for i in ti.lk.other}, v5)[1] if ti.is_range and v5 is not None else r5
    mk(["C5"], "used", k5, r5, {"change": _pct_term(ti.pct.get(k5)), "story": named5},
       f"Quote a policy that uses the {disp} table and check that the changed factor is applied.",
       "A table can load correctly and still never be picked up by the rating logic.",
       ti.inputs(r5, v5), ti.expect(exp_row, ti.pct.get(k5)))
    # C6: range edges in the bands that changed the most
    if ti.is_range:
        assert ti.lk.mi is not None
        bands = sk[:2] if ti.pct else ([sk[0], sk[-1]] if len(sk) > 1 else sk)
        integer = ti.a.key_columns()[ti.lk.mi].type == ColumnType.INTEGER
        for k in bands:
            r = ti.a_rows[k]
            lo = ti.edge_value(r)
            if lo is None:
                continue
            other = {i: r.key[i].value for i in ti.lk.other}
            prev_txt = ""
            if integer:
                prev_row = ti.lk.factor(other, lo - 1)[1]
                if prev_row is not None and prev_row is not r:
                    prev_txt = f" One step lower ({ti.label(ti.lk.mi)} = {_num(lo - 1)}) should show the previous band: {ti.factor_value(prev_row)}."
            mk(["C6"], "edge", k, r, {"edge": 15.0, "change": _pct_term(ti.pct.get(k)), "story": named5},
               f"Quote exactly on the lower edge of a {disp} band, where the factor changes.",
               "The most common band error is off by one, which gives the wrong rate to policies right on the edge.",
               ti.inputs(r, lo), ti.expect(r, ti.pct.get(k)) + prev_txt)
    # C7: wildcard / default row
    wild = [r for r in ti.a.rows if any(c.kind == "WILDCARD" for c in r.key)]
    if wild:
        rw = sorted(wild, key=lambda r: str(row_key(r, ti.order)))[0]
        kw = row_key(rw, ti.order)
        mk(["C7"], "wild", kw, rw, {"wildcard": 10.0, "change": _pct_term(ti.pct.get(kw)), "story": named5},
           f"Quote a policy with a value that is not listed in {disp}, so the catch-all row applies.",
           "Blank or wrong catch-all rows have caused production defects.",
           ti.inputs(rw), ti.expect(rw, ti.pct.get(kw)))
    # C8: new key, or whole new table
    new_table = ti.has_baseline and p.baseline is None and p.export is not None
    if ti.added or new_table:
        kn = ti.added[0] if ti.added else sk[len(sk) // 2]
        rn = ti.a_rows[kn]
        vn = ti.edge_value(rn) if ti.is_range else None
        mk(["C8"], "new", kn, rn, {"new": 20.0, "story": named5},
           f"Quote a policy that uses the new {'table' if new_table and not ti.added else 'row'} in {disp}.",
           "A new value that was never rated before is the easiest place to find a missing or wrong rate.",
           ti.inputs(rn, vn), ti.expect(rn, None))
    return out


def _fix_candidates(res: RunResult, infos: dict[str, _TableInfo]) -> list[Cand]:
    worst: dict[str, Any] = {}
    for f in sorted(res.findings, key=lambda x: x.sort_key()):
        if f.rule_id in FIX_RULES and f.table_id and f.level in ("BLOCKER", "CRITICAL", "HIGH", "WARNING") and f.table_id not in worst:
            worst[f.table_id] = f
    out: list[Cand] = []
    for tid, f in sorted(worst.items()):
        ti = infos.get(tid)
        disp = ti.pair.display if ti else (f.table_name or tid)
        where = f.key or "the value named in the finding"
        approved = f.approved_value or "the approved value"
        if ti is not None:
            hit = next((k for k in ti.a_rows if key_text(ti.a, k) == f.key), None)
            if hit is not None:
                row = ti.a_rows[hit]
                v = ti.edge_value(row) if ti.is_range else None
                where = ti.inputs(row, v)
                approved = ti.factor_value(row)
        out.append(Cand(
            id=_tid("MT", "C10", tid, f.key, f.column), cats=["C10"], score=40.0 + (5.0 if (ti and ti.named) else 0.0), table_id=tid, table=disp,
            what=f"After the fix, quote the exact row that was wrong in {disp}.",
            why="This proves the specific mismatch is fixed, not just that the table loads.",
            setup=where, expect=f"In the rating worksheet, the {ti.header if ti else 'factor'} for {disp} should be {approved}.",
            source=f"This run: {f.approved_ref and pretty_cell(f.approved_ref) or 'see the Discrepancies sheet'}",
            named=bool(ti and ti.named), parts={"fix": 40.0, "story": 5.0 if (ti and ti.named) else 0.0}))
    return out


def _date_candidates(res: RunResult, any_tables: bool, premium_tables: bool) -> list[Cand]:
    jira = res.jira
    fdate = jira.filing_effective_date
    rn = jira.renewal_effective_date or fdate
    tcfg = aux("tests")
    rule = tcfg.get("midterm_ratebook_rule", "rate book of the term inception")
    prior = res.baseline_info.get("edition") or "the previous edition"
    new_ed = res.summary.get("export_edition") or "the new edition"
    ph = "the filing date"
    out: list[Cand] = []
    src_f = f"Jira {jira.ticket_key or 'story'}: Filing Effective Date" + (f" {_fmt_date(fdate, '')}" if fdate else " (not found in the story)")

    def c(cat: str, variant: str, score: float, what: str, why: str, setup: str, exp: str, parts: dict[str, float], src: str = src_f) -> None:
        out.append(Cand(id=_tid("MT", cat, variant), cats=[cat], score=score, table_id="", table="", what=what, why=why, setup=setup, expect=exp,
                        source=src, named=True, parts=parts))

    c("C1", "before", 30.0, f"Quote a new business policy effective the day before {ph}.",
      "If the date rule is wrong, quotes on the wrong side of the filing date are over- or undercharged.",
      f"New business, effective {_shift(fdate, -1, '<filing date>')}.",
      f"The rating worksheet should use the old rates (rate book {prior}).", {"dates": 25.0, "story": 5.0})
    c("C1", "on", 30.0, f"Quote a new business policy effective on {ph}.",
      "This is the first day the new rates must apply.",
      f"New business, effective {_shift(fdate, 0, '<filing date>')}.",
      f"The rating worksheet should use the new rates (rate book {new_ed}).", {"dates": 25.0, "story": 5.0})
    if not jira.transaction_scope or "RENEWAL" in jira.transaction_scope:
        c("C2", "renewal", 25.0 + (5.0 if "RENEWAL" in jira.transaction_scope else 0.0),
          "Renew a policy with an effective date on or after the filing date.",
          "Renewals must pick up the new rates, and they often follow a different date path than new business.",
          f"Renewal, effective {_shift(rn, 0, '<renewal date>')}.", f"The rating worksheet should use the new rates (rate book {new_ed}).",
          {"dates": 25.0, "story": 5.0 if "RENEWAL" in jira.transaction_scope else 0.0}, src_f)
    c("C3", "midterm", 25.0, "Make a mid-term policy change on a policy whose term started before the filing date.",
      "Existing customers should keep their original rates until renewal. This is the confirmed company rule.",
      f"A policy that started before {_fmt_date(fdate, ph)}; add a premium-bearing change effective on or after {_fmt_date(fdate, ph)}.",
      f"The rating worksheet should keep the original rates ({rule}), not the new ones.", {"dates": 25.0},
      f"Company rule: {rule}")
    text = res.jira_text or ""
    m4, m9 = bool(CHANGE_RE.search(text)), bool(OTHER_RE.search(text))
    if premium_tables:
        c("C4", "nonpremium", float(CONTROL_BASE) + (5.0 if m4 else 0.0), "Make a change that has no premium effect, such as correcting an address, on a policy in this filing.",
          "A change that should not cost anything must not move the premium.",
          f"A policy in the changed rate book; change only the address, effective on or after {_fmt_date(fdate, ph)}.",
          "Premium unchanged.", {"control": float(CONTROL_BASE), **({"story": 5.0} if m4 else {})},
          "Jira story mentions endorsements or policy changes" if m4 else "Not mentioned in the story")
        out[-1].excluded = "" if m4 else "The story does not mention endorsements or policy changes."
    c("C9", "control", float(CONTROL_BASE) + (5.0 if m9 else 0.0), "Run the same quote in a state or line that is not part of this change.",
      "A rate change must not leak into other states or lines.",
      f"The same risk as above, but in a state or line other than {', '.join(jira.states) or 'the one in the story'}; same effective date.",
      "Premium and factors unchanged from before this change.", {"control": float(CONTROL_BASE), **({"story": 5.0} if m9 else {})},
      "Jira story mentions other states or lines" if m9 else "Not mentioned in the story")
    out[-1].excluded = "" if m9 else "The story does not mention other states or lines."
    if FEES_RE.search(res.jira_text or ""):
        c("C11", "cancel", float(CONTROL_BASE) + 5.0, "Flat-cancel a policy that carries the new rates, fees or surcharges.",
          "The story mentions fees, surcharges or cancellations, and these must reverse in full, exactly once.",
          "Issue a policy on or after the filing date, then cancel it flat the same day.",
          "Premium and fees both reverse fully, once, with nothing left over.", {"control": float(CONTROL_BASE), "story": 5.0},
          "Jira story mentions fees, surcharges or cancellations")
    return out


def _combine(cands: list[Cand]) -> None:
    """One quote on the filing date can prove the date rule and a changed factor at once."""
    on = next((c for c in cands if c.cats == ["C1"] and c.id == _tid("MT", "C1", "on")), None)
    donors = [c for c in cands if c.cats[0] in ("C5", "C6")]
    if on is None or not donors:
        return
    best = sorted(donors, key=lambda c: (-c.score, not c.named, c.id))[0]
    on.cats = ["C1", best.cats[0]]
    on.score += best.score
    on.table_id, on.table = best.table_id, best.table
    on.what = f"Quote on the filing date using the changed {best.table} table."
    on.why = "One quote proves the new rate book starts on the right day and that the changed table is used in rating."
    on.setup = f"New business, effective {on.setup.split('effective ')[-1].rstrip('.')}; {best.setup}."
    lead = "In the rating worksheet, the "
    tail = best.expect[len(lead):] if best.expect.startswith(lead) else best.expect
    on.expect = f"{on.expect.rstrip('.')}. Also, the {tail}"
    on.source = f"{on.source}; {best.source}"
    on.parts = {**on.parts, **{f"combined {k}": v for k, v in best.parts.items()}}
    on.note = f"Combines the date check with test {best.id} ({best.cat})."
    best.state, best.note = "combined", f"Combined into {on.id}"


def select(cands: list[Cand], cap: int) -> list[Cand]:
    """Pick the Must Test list: fixes first, one date test always, max 2 per table, at least 3."""
    live = [c for c in cands if c.state != "combined"]
    for c in live:
        reason = c.excluded or ("Score below 20." if c.score < MIN_SCORE and c.cats[0] != "C10" else "")
        if reason:
            c.state, c.note = "cut", reason
    live = [c for c in live if c.state != "cut"]
    live.sort(key=lambda c: (0 if c.cats[0] == "C10" else 1, -c.score, 0 if c.named else 1, c.id))
    chosen: list[Cand] = [c for c in live if c.cats[0] == "C10"][:HARD_CAP]
    limit = min(HARD_CAP, max(cap, len(chosen)))
    per_table: Counter[str] = Counter()
    if not any(c.cats[0] == "C1" for c in chosen):
        first_c1 = next((c for c in live if c.cats[0] == "C1"), None)
        if first_c1 is not None and len(chosen) < limit:
            chosen.append(first_c1)
    for c in live:
        if c in chosen:
            if c.table_id and c.cats[0] != "C10":
                per_table[c.table_id] += 1
            continue
        if len(chosen) >= limit:
            c.state, c.note = "cut", "Below the cut-off for the Must Test list."
            continue
        if c.table_id and per_table[c.table_id] >= 2:
            c.state, c.note = "cut", "Table already has 2 tests."
            continue
        chosen.append(c)
        if c.table_id:
            per_table[c.table_id] += 1
    chosen.sort(key=lambda c: (0 if c.cats[0] == "C10" else 1, -c.score, 0 if c.named else 1, c.id))
    for c in chosen:
        c.state, c.note = "selected", c.note if c.note.startswith("Combines") else ""
    return chosen


def build_must(res: RunResult, suite: TestSuite) -> None:
    cfg = res.cfg
    fmap: dict[str, str] = aux("field_map").get("fields", {})
    cap = cfg.tests.max_must_tests
    infos: dict[str, _TableInfo] = {}
    cands: list[Cand] = []
    for p in sorted(res.mapping.pairs, key=lambda x: x.id):
        if p.approved is None or p.jira_action == "BYPASS":
            continue
        d = res.deltas.get(p.id)
        if d is not None and p.export is not None and d.empty:
            continue
        ti = _TableInfo(res, p, fmap)
        if not ti.keys:
            continue
        infos[p.id] = ti
        cands += _table_candidates(res, ti)
    cands += _fix_candidates(res, infos)
    cands += _date_candidates(res, bool(infos), bool(infos))
    _combine(cands)
    chosen = select(cands, cap)
    ids = {c.id: i for i, c in enumerate(chosen, 1)}
    suite.must = [{
        "priority": ids[c.id], "test_id": c.id, "what": c.what, "why": c.why, "setup": c.setup, "expect": c.expect,
        "source": c.source, "category": c.cat, "table": c.table, "score": round(c.score, 1),
    } for c in chosen]
    order = {c.id: n for n, c in enumerate(sorted(cands, key=lambda c: (0 if c.cats[0] == "C10" else 1, -c.score, c.id)), 1)}
    suite.candidates = [{
        "selected": {"selected": "Yes", "combined": "Combined", "cut": "No"}.get(c.state, "No"), "priority": ids.get(c.id, ""), "test_id": c.id,
        "category": c.cat, "score": round(c.score, 1), "table": c.table, "what": c.what, "why": c.why, "setup": c.setup, "expect": c.expect,
        "source": c.source, "note": c.note, "score_parts": "; ".join(f"{k} {v:g}" for k, v in c.parts.items()),
    } for c in sorted(cands, key=lambda c: order[c.id])]
    if any(c.cats[0] == "C10" for c in cands) and len([c for c in cands if c.cats[0] == "C10"]) > HARD_CAP:
        suite.assumptions.append(f"More than {HARD_CAP} tables have mismatches; only the first {HARD_CAP} retests are listed.")
    if res.jira.filing_effective_date is None:
        suite.assumptions.append("The story has no filing effective date, so date tests show '<filing date>' with a day offset.")
