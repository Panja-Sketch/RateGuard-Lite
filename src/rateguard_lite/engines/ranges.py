"""Engine D: range integrity + boundary shift detection (spec 8.4)."""

from __future__ import annotations

from decimal import Decimal

from ..learning.context import LearnedContext
from ..mapping.tables import TablePair
from ..model.core import ColumnRole, RateTable, Row
from ..model.findings import Finding, Severity, Status
from .common import KeyT, make_finding, ref, to_decimal_safe
from .factors import FactorResult


def _range_positions(t: RateTable) -> tuple[list[int], list[int], list[int]]:
    cols = t.key_columns()
    mins = [i for i, c in enumerate(cols) if c.role == ColumnRole.RANGE_MIN]
    maxs = [i for i, c in enumerate(cols) if c.role == ColumnRole.RANGE_MAX]
    others = [i for i, c in enumerate(cols) if c.role == ColumnRole.KEY]
    return mins, maxs, others


def _num(r: Row, i: int) -> Decimal | None:
    c = r.key[i]
    return None if c.kind != "VALUE" else to_decimal_safe(c.value)


def _places(d: Decimal) -> int:
    exp = d.as_tuple().exponent
    return max(0, -exp) if isinstance(exp, int) else 0


def check_ranges(t: RateTable, pair_id: str, display: str, side: str, learned: LearnedContext | None = None) -> list[Finding]:
    mins, maxs, others = _range_positions(t)
    if not mins or len(mins) != len(maxs):
        return []
    out: list[Finding] = []
    mi, ma = mins[0], maxs[0]
    cols = t.key_columns()
    conv = (learned.range_conventions.get(pair_id) if learned else None) or cols[mi].range_convention
    explicit = conv is not None
    if conv is None:
        conv = "INCLUSIVE_INCLUSIVE" if cols[mi].type.value == "INTEGER" else "INCLUSIVE_EXCLUSIVE"
    groups: dict[tuple[str | None, ...], list[Row]] = {}
    for r in t.rows:
        groups.setdefault(tuple(r.key[i].value if r.key[i].kind == "VALUE" else "*" for i in others), []).append(r)

    def label(g: tuple[str | None, ...]) -> str:
        return ", ".join(f"{cols[i].source_header}={v}" for i, v in zip(others, g, strict=True)) or "all rows"

    def fnd(rule: str, sev: Severity | None, msg: str, row: Row, st: Status | None = None) -> None:
        out.append(make_finding(rule, severity=sev, status=st, table_id=pair_id, table_name=display, message=f"[{side}] {msg}",
                                key=f"{cols[mi].source_header}={row.key[mi].raw}", export_ref=ref(t, row.key[mi]) if side == "export" else "",
                                approved_ref=ref(t, row.key[mi]) if side == "approved" else ""))

    for g, rows in sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        bands = []
        for r in rows:
            lo, hi = _num(r, mi), _num(r, ma)
            bands.append((lo, hi, r))
        bands.sort(key=lambda b: (b[0] is not None, b[0] if b[0] is not None else Decimal(0)))
        for lo, hi, r in bands:
            if lo is not None and hi is not None and lo > hi:
                fnd("RANGE_MIN_GT_MAX", Severity.CRITICAL, f"{label(g)}: minimum {lo} is greater than maximum {hi}.", r)
        adj_eq = adj_step = 0
        if not explicit and conv == "INCLUSIVE_EXCLUSIVE":
            for (_, ph, _), (nl, _, _) in zip(bands, bands[1:], strict=False):
                if ph is not None and nl is not None:
                    if nl == ph:
                        adj_eq += 1
                    elif nl == ph + Decimal(1).scaleb(-_places(ph)):
                        adj_step += 1
            if adj_eq and adj_step:
                fnd("RANGE_GAP", None, f"{label(g)}: band boundaries mix both conventions; confirm the range convention.", bands[0][2],
                    Status.NEEDS_REVIEW)
                continue
            if adj_step and not adj_eq:
                conv = "INCLUSIVE_INCLUSIVE"
        for (_pl, ph, _pr), (nl, _nh, nr) in zip(bands, bands[1:], strict=False):
            if ph is None:
                fnd("RANGE_OVERLAP", Severity.HIGH, f"{label(g)}: an open-ended band is followed by another band.", nr)
                continue
            if nl is None:
                continue
            step = Decimal(1) if conv == "INCLUSIVE_INCLUSIVE" and cols[mi].type.value == "INTEGER" else Decimal(1).scaleb(-_places(ph))
            expected = ph + step if conv == "INCLUSIVE_INCLUSIVE" else ph
            if nl > expected:
                fnd("RANGE_GAP", Severity.HIGH, f"{label(g)}: gap between {ph} and {nl} (next band should start at {expected}).", nr)
            elif nl < expected:
                fnd("RANGE_OVERLAP", Severity.HIGH, f"{label(g)}: band starting at {nl} overlaps the previous band ending at {ph}.", nr)
        last = bands[-1]
        if last[2].key[ma].kind == "BLANK":
            fnd("OPEN_END_MISSING", Severity.WARNING, f"{label(g)}: the last band has no maximum and blank is not treated as open-ended.", last[2])
    return out


def boundary_shifts(pair: TablePair, res: FactorResult) -> list[Finding]:
    a, e = pair.approved, pair.export
    if a is None or e is None or res.a_idx is None or res.e_idx is None or not res.missing or not res.extra:
        return []
    amins, amaxs, aoth = _range_positions(a)
    emins, emaxs, eoth = _range_positions(e)
    if not amins or len(amins) != len(emins) or len(amaxs) != len(emaxs):
        return []
    # work in sorted-canonical-name key space: map each range column name to its position in the key tuple
    names = sorted(c.canonical_name for c in a.key_columns())
    acols, ecols = a.key_columns(), e.key_columns()
    a_pos = {c.canonical_name: i for i, c in enumerate(acols)}
    e_pos = {c.canonical_name: i for i, c in enumerate(ecols)}
    lo_name, hi_name = acols[amins[0]].canonical_name, acols[amaxs[0]].canonical_name
    oth_names = [acols[i].canonical_name for i in aoth]
    fac = sorted(c.canonical_name for c in a.factor_columns())

    def sig(t: RateTable, row: Row, pos: dict[str, int]) -> tuple[object, ...]:
        return (tuple(row.key[pos[n]].value for n in oth_names), tuple(row.factors[f].value if f in row.factors else None for f in fac))

    extras: dict[tuple[object, ...], list[tuple[KeyT, Row]]] = {}
    for ek in res.extra:
        r = res.e_idx.rows[ek]
        extras.setdefault(sig(e, r, e_pos), []).append((ek, r))
    out: list[Finding] = []
    del names
    for mk in res.missing:
        ar = res.a_idx.rows[mk]
        alo, ahi = _num(ar, a_pos[lo_name]), _num(ar, a_pos[hi_name])
        for _, er in extras.get(sig(a, ar, a_pos), []):
            elo, ehi = _num(er, e_pos[lo_name]), _num(er, e_pos[hi_name])
            dl = None if alo is None or elo is None else abs(alo - elo)
            dh = None if ahi is None or ehi is None else abs(ahi - ehi)
            if (dl in (0, 1) or dl is None) and (dh in (0, 1) or dh is None) and (dl == 1 or dh == 1):
                out.append(make_finding(
                    "OFF_BY_ONE_BOUNDARY", severity=Severity.CRITICAL, table_id=pair.id, table_name=pair.display,
                    key=f"{acols[amins[0]].source_header}={ar.key[a_pos[lo_name]].raw}",
                    approved_value=f"[{ar.key[a_pos[lo_name]].raw} .. {ar.key[a_pos[hi_name]].raw or 'open'}]",
                    export_value=f"[{er.key[e_pos[lo_name]].raw} .. {er.key[e_pos[hi_name]].raw or 'open'}]",
                    approved_ref=ref(a, ar.key[a_pos[lo_name]]), export_ref=ref(e, er.key[e_pos[lo_name]]),
                    message="A band boundary is shifted by exactly one versus the approved band (same factor)."))
                break
    return out
