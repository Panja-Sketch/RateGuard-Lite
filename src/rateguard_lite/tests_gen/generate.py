"""Smart test-scenario generator (spec 10). Produces a QA script; it does not execute quotes."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from ..config import aux
from ..engines.common import key_order, key_text, row_key, to_decimal_safe
from ..model.core import ColumnRole, ColumnType, RateTable, Row

if TYPE_CHECKING:
    from ..pipeline import RunResult


@dataclass
class TestSuite:
    parameter: list[dict[str, Any]] = field(default_factory=list)
    transaction: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


def _tid(prefix: str, *parts: str) -> str:
    return prefix + "-" + hashlib.sha1("|".join(parts).encode()).hexdigest()[:8].upper()


def _field_label(name: str, fmap: dict[str, str]) -> str:
    return fmap.get(name, name)


class _Lookup:
    """Expected-factor lookup computed from the approved table."""

    def __init__(self, t: RateTable) -> None:
        cols = t.key_columns()
        self.t = t
        self.cols = cols
        self.mi = next((i for i, c in enumerate(cols) if c.role == ColumnRole.RANGE_MIN), None)
        self.ma = next((i for i, c in enumerate(cols) if c.role == ColumnRole.RANGE_MAX), None)
        self.other = [i for i, c in enumerate(cols) if c.role == ColumnRole.KEY]
        self.fname = [c.canonical_name for c in t.factor_columns()]

    def factor(self, other_vals: dict[int, str | None], value: Decimal | None) -> tuple[str, Row | None]:
        best: Row | None = None
        for r in self.t.rows:
            ok = True
            for i in self.other:
                c = r.key[i]
                want = other_vals.get(i)
                if c.kind == "WILDCARD":
                    continue
                if want is None or c.value != want:
                    ok = False
                    break
            if not ok:
                continue
            if self.mi is not None and self.ma is not None and value is not None:
                lo = to_decimal_safe(r.key[self.mi].value) if r.key[self.mi].kind == "VALUE" else None
                hi = to_decimal_safe(r.key[self.ma].value) if r.key[self.ma].kind == "VALUE" else None
                if lo is not None and value < lo:
                    continue
                if hi is not None and value > hi:
                    continue
            best = r
            break
        if best is None:
            return "no matching band (expect a validation error or the highest band; verify)", None
        return ", ".join(f"{n}={best.factors[n].raw}" for n in self.fname if n in best.factors), best


def _mid(lo: Decimal, hi: Decimal) -> Decimal:
    return (lo + hi) / 2


def generate_tests(res: RunResult) -> TestSuite:
    cfg = res.cfg
    fmap: dict[str, str] = aux("field_map").get("fields", {})
    tcfg = aux("tests")
    suite = TestSuite()
    cap = cfg.tests.max_per_table
    oob_expect = tcfg.get("out_of_bounds_expectation", "highest band factor or validation error")

    for p in sorted(res.mapping.pairs, key=lambda x: x.id):
        a = p.approved
        if a is None or p.jira_action == "BYPASS":
            continue
        d = res.deltas.get(p.id)
        lk = _Lookup(a)
        order = key_order(a)
        a_rows = {row_key(r, order): r for r in a.rows}
        # keys to test
        keys: list[tuple[Any, ...]] = []
        pct: dict[tuple[Any, ...], float] = {}
        if d is not None and p.export is not None:
            if d.empty:
                continue
            keys = [k for k in (d.changed + d.added) if k in a_rows]
        else:
            keys = list(a_rows)
        if not keys:
            continue
        prio_keys = keys
        if d is not None and p.baseline is not None and p.baseline.rows:
            from ..engines.common import build_index
            bidx = build_index(p.baseline).rows
            for k in keys:
                old = bidx.get(k)
                new = a_rows[k]
                try:
                    f = next(iter(new.factors))
                    o, n = to_decimal_safe(old.factors[f].value) if old else None, to_decimal_safe(new.factors[f].value)
                    pct[k] = abs(float((n - o) / o)) if (o and n is not None and o != 0) else 9.9
                except (KeyError, StopIteration, ZeroDivisionError):
                    pct[k] = 9.9
            prio_keys = sorted(keys, key=lambda k: (-pct.get(k, 0), str(k)))
        else:
            prio_keys = sorted(keys, key=lambda k: str(k))
            # prefer first and last bands
            if len(prio_keys) > cap:
                half = cap // 2
                prio_keys = prio_keys[:half] + prio_keys[-(cap - half):]
        prio_keys = prio_keys[:cap]
        emitted = 0
        has_range = lk.mi is not None and lk.ma is not None
        for k in prio_keys:
            row = a_rows[k]
            other_vals = {i: row.key[i].value for i in lk.other}
            src = f"{a.source.file}!{a.source.sheet}!{row.key[0].coord if row.key else ''}"
            change_note = f"changed by {pct[k]*100:.1f}%" if k in pct and pct[k] < 9 else ("new/changed row" if d is not None else "approved value")
            prio = "P1" if (k in pct and pct[k] >= 0.05) or d is None or (d is not None and k in d.added) else "P2"
            if has_range:
                assert lk.mi is not None and lk.ma is not None
                lo = to_decimal_safe(row.key[lk.mi].value) if row.key[lk.mi].kind == "VALUE" else None
                hi = to_decimal_safe(row.key[lk.ma].value) if row.key[lk.ma].kind == "VALUE" else None
                integer = a.key_columns()[lk.mi].type == ColumnType.INTEGER
                step = Decimal(1) if integer else Decimal("0.01")
                pts: list[tuple[str, Decimal]] = []
                if lo is not None:
                    pts.append(("band minimum", lo))
                if hi is not None:
                    pts.append(("band maximum", hi))
                if integer:
                    if lo is not None:
                        pts.append(("one below band minimum (previous band)", lo - 1))
                    if hi is not None:
                        pts.append(("one above band maximum (next band)", hi + 1))
                elif lo is not None and hi is not None:
                    pts = [("band minimum", lo), ("band midpoint", _mid(lo, hi)), ("just below band maximum", hi - step)]
                if lo is None and hi is None:
                    continue
                for label, v in pts:
                    exp, _ = lk.factor(other_vals, v)
                    params = {c.canonical_name: (row.key[i].value if c.role == ColumnRole.KEY else None) for i, c in enumerate(a.key_columns())}
                    params[a.key_columns()[lk.mi].canonical_name] = format(v.normalize(), "f")
                    params.pop(a.key_columns()[lk.ma].canonical_name, None)
                    pt = "; ".join(f"{_field_label(n, fmap)}={'<any value not listed>' if v2 is None else v2}" for n, v2 in params.items())
                    obj = f"Boundary test at {label}"
                    suite.parameter.append({
                        "test_id": _tid("PT", p.id, key_text(a, k), obj, pt), "priority": "P1" if "minimum" in label or "maximum" in label else prio,
                        "objective": obj, "table": f"{p.display} ({p.id})", "key_parameters": pt, "expected_factor": exp,
                        "source_cell": src, "check": f"In the rating worksheet confirm the {a.factor_columns()[0].source_header if a.factor_columns() else 'factor'} applied is {exp}. ({change_note})"})
                    emitted += 1
            else:
                params = {c.canonical_name: (row.key[i].value if row.key[i].kind == "VALUE" else None) for i, c in enumerate(a.key_columns())}
                exp = ", ".join(f"{n}={row.factors[n].raw}" for n in lk.fname if n in row.factors)
                wild = any(v is None for v in params.values())
                pt = "; ".join(f"{_field_label(n, fmap)}={'<any value not listed>' if v is None else v}" for n, v in params.items())
                obj = "Wildcard row: use a value that is not explicitly listed" if wild else "Changed factor lands in rating"
                suite.parameter.append({
                    "test_id": _tid("PT", p.id, key_text(a, k), obj), "priority": "P3" if wild else prio, "objective": obj,
                    "table": f"{p.display} ({p.id})", "key_parameters": pt, "expected_factor": exp, "source_cell": src,
                    "check": f"In the rating worksheet confirm the factor applied is {exp}. ({change_note})"})
                emitted += 1
        if has_range and lk.mi is not None and lk.ma is not None:
            maxes = [to_decimal_safe(r.key[lk.ma].value) for r in a.rows if r.key[lk.ma].kind == "VALUE"]
            if maxes and all(m is not None for m in maxes):
                top = max(m for m in maxes if m is not None)
                col_mi = a.key_columns()[lk.mi].canonical_name
                suite.parameter.append({
                    "test_id": _tid("PT", p.id, "oob"), "priority": "P3", "objective": "Out-of-bounds: above the highest band",
                    "table": f"{p.display} ({p.id})", "key_parameters": f"{_field_label(col_mi, fmap)}={format((top + 1).normalize(), 'f')}",
                    "expected_factor": oob_expect, "source_cell": f"{a.source.file}!{a.source.sheet}", "check": "Confirm the system applies the top band or blocks the quote."})
        if emitted == 0 and not has_range:
            continue
    suite.parameter.sort(key=lambda r: (r["priority"], r["table"], r["test_id"]))

    # ---- transaction & date matrix
    tpl = tcfg.get("templates", {})
    fdate = res.jira.filing_effective_date
    rn = res.jira.renewal_effective_date or fdate
    prior = res.baseline_info.get("edition") or "prior edition"
    new_ed = res.summary.get("export_edition") or "new edition"
    rule = tcfg.get("midterm_ratebook_rule", "rate book of the term inception")
    suite.assumptions += [
        f"Mid-term policy changes use the {rule}. This is an assumption to verify with the rating owner.",
        "The tool compares rate tables only; it does not execute quotes. Expected factors come from the approved workbook.",
        "Rate book selection (which edition applies on a given date) is carrier-specific; confirm the expected edition per scenario.",
    ]
    if fdate is None:
        suite.assumptions.append("No filing effective date was found in Jira, so transaction dates are shown as <filing date> offsets.")

    def dshift(iso: str | None, days: int, label: str) -> str:
        if iso is None:
            return f"{label}{days:+d} day(s)" if days else label
        return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()

    rows = [
        ("TX-01", "New Business", dshift(fdate, -1, "<filing date>"), f"{prior} (prior edition)", "P1", "Quote just before the filing date uses the old rates."),
        ("TX-02", "New Business", dshift(fdate, 0, "<filing date>"), f"{new_ed} (new edition)", "P1", "Quote on the filing date uses the new rates."),
        ("TX-03", "Renewal", dshift(rn, 0, "<renewal date>"), f"{new_ed} (new edition)", "P1", "Renewal effective on/after the renewal date uses the new rates."),
        ("TX-04", "Renewal", dshift(rn, -1, "<renewal date>"), f"{prior} (prior edition)", "P2", "Renewal just before the renewal date uses the old rates."),
        ("TX-05", "Policy change (mid-term) on a term incepted before the filing date", dshift(fdate, 0, "<on/after filing date>"), f"{rule} - verify", "P2", "Rate book used for a mid-term change follows the configured rule."),
        ("TX-06", "Out-of-sequence endorsement on a pre-filing term", dshift(fdate, 0, "<on/after filing date>"), f"{rule} - verify", "P2", "Backdated endorsement rates with the same rule as TX-05."),
        ("TX-07", "Non-premium-bearing policy change (e.g. address correction)", "any", "premium change must be $0", "P1", "Premium must not move on a non-premium-bearing change."),
        ("TX-08", "Flat cancellation", "any", "all premium and fees reversed exactly once", "P1", "Premium and fees reverse exactly once, with no residual."),
    ]
    for tid, tx, eff, exp, pr, chk in rows:
        if not tpl.get(tid, True):
            continue
        suite.transaction.append({"test_id": tid, "priority": pr, "transaction": tx, "policy_effective_date": eff,
                                  "expected_rate_book": exp, "what_to_check": chk})
    return suite
