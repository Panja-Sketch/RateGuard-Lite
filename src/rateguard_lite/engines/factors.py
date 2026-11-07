"""Engine A: cell-level factor comparison, approved vs export (spec 8.1). Never compares by row position."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..config import Config
from ..ingest.normalize import quantize
from ..mapping.tables import TablePair
from ..model.core import Row
from ..model.findings import Finding, Severity, Status
from .common import Index, KeyT, build_index, key_names, key_text, make_finding, ref, to_decimal_safe

MAX_ROUNDED_FINDINGS = 500


@dataclass
class FactorResult:
    findings: list[Finding] = field(default_factory=list)
    stats: Counter[str] = field(default_factory=Counter)
    missing: list[KeyT] = field(default_factory=list)
    extra: list[KeyT] = field(default_factory=list)
    a_idx: Index | None = None
    e_idx: Index | None = None
    abs_tolerance_note: str = ""


def _digits(d: Decimal) -> str:
    return "".join(ch for ch in format(d, "f") if ch.isdigit())


def _is_transposition(x: str, y: str) -> bool:
    if len(x) != len(y) or x == y or sorted(x) != sorted(y):
        return False
    return sum(1 for p, q in zip(x, y, strict=True) if p != q) == 2


def compare_values(a: Decimal, e: Decimal, scale: int, cfg: Config, abs_tol: Decimal) -> tuple[str, dict[str, str]]:
    """Return (rule, details). Rules: MATCH, MATCH_ROUNDED, PRECISION_TRUNCATION (also rounded),
    ORDER_OF_MAGNITUDE, TRANSPOSITION, SIGN_FLIP, VALUE_MISMATCH."""
    if a == e:
        return "MATCH", {}
    if abs_tol > 0 and abs(a - e) <= abs_tol:
        return "MATCH", {"abs_tolerance": str(abs_tol)}
    a_r = quantize(a, scale)
    if a_r == e:
        det = {"approved_rounded": str(a_r), "scale": str(scale)}
        if a != 0 and abs(a - a_r) / abs(a) > Decimal(str(cfg.comparison.precision_materiality)):
            return "PRECISION_TRUNCATION", det
        return "MATCH_ROUNDED", det
    delta = e - a
    det = {"delta": format(delta.normalize(), "f"), "pct": (f"{(delta / a * 100):.2f}%" if a != 0 else "n/a")}
    if a != 0 and e != 0:
        hi, lo = max(abs(a), abs(e)), min(abs(a), abs(e))
        if hi / lo >= Decimal(str(cfg.comparison.magnitude_ratio)):
            return "ORDER_OF_MAGNITUDE", det
        if _is_transposition(_digits(a_r), _digits(e)) and (a < 0) == (e < 0):
            return "TRANSPOSITION", det
        if (a < 0) != (e < 0):
            return "SIGN_FLIP", det
    return "VALUE_MISMATCH", det


_SEV = {
    "ORDER_OF_MAGNITUDE": Severity.CRITICAL, "TRANSPOSITION": Severity.CRITICAL, "SIGN_FLIP": Severity.CRITICAL,
    "VALUE_MISMATCH": Severity.CRITICAL, "PRECISION_TRUNCATION": Severity.WARNING, "MATCH_ROUNDED": Severity.INFO,
}


def _wildcard_pairs(missing: list[KeyT], extra: list[KeyT]) -> tuple[list[tuple[KeyT, KeyT]], set[KeyT], set[KeyT]]:
    masked: dict[tuple[int, KeyT], list[KeyT]] = {}
    for ek in extra:
        for i in range(len(ek)):
            masked.setdefault((i, ek[:i] + (("", None),) + ek[i + 1:]), []).append(ek)
    pairs, used_m, used_e = [], set(), set()
    for mk in missing:
        for i in range(len(mk)):
            for ek in masked.get((i, mk[:i] + (("", None),) + mk[i + 1:]), []):
                if ek in used_e:
                    continue
                if (mk[i][0] == "WILDCARD") != (ek[i][0] == "WILDCARD"):
                    pairs.append((mk, ek))
                    used_m.add(mk)
                    used_e.add(ek)
                    break
            if mk in used_m:
                break
    return pairs, used_m, used_e


def compare_pair(pair: TablePair, cfg: Config) -> FactorResult:
    a, e = pair.approved, pair.export
    assert a is not None and e is not None
    res = FactorResult()
    tid, tname = pair.id, pair.display

    def nm(rule: str, msg: str) -> None:
        res.findings.append(make_finding(rule, status=Status.NEEDS_MAPPING, table_id=tid, table_name=tname, message=msg))

    if key_names(a) != key_names(e):
        nm("NEEDS_MAPPING", f"Key columns differ: approved {key_names(a)} vs export {key_names(e)}. Confirm column mapping.")
        return res
    a_f = {c.canonical_name: c for c in a.factor_columns()}
    e_f = {c.canonical_name: c for c in e.factor_columns()}
    miss_cols = sorted(set(a_f) - set(e_f))
    if miss_cols or not a_f:
        nm("NEEDS_MAPPING", f"Factor column(s) {miss_cols or '[none detected]'} of the approved table not found in the export. Confirm column mapping.")
        return res
    common = sorted(a_f)
    ov = cfg.comparison.table_overrides.get(tid, {})
    abs_tol = Decimal(str(ov.get("abs_tolerance", cfg.comparison.abs_tolerance)))
    if abs_tol > 0:
        res.abs_tolerance_note = f"{tid}: abs_tolerance={abs_tol}"
    strike_delete = cfg.approved.strikethrough_means == "delete"
    a_idx = build_index(a)
    e_idx = build_index(e)
    res.a_idx, res.e_idx = a_idx, e_idx
    struck: set[KeyT] = set()
    if strike_delete:
        struck = {k for k, r in a_idx.rows.items() if any("STRIKETHROUGH" in c.flags for c in r.key)}

    rounded_emitted = 0
    for k, arow in a_idx.rows.items():
        if k in struck:
            continue
        erow = e_idx.rows.get(k)
        if erow is None:
            res.missing.append(k)
            continue
        for fname in common:
            ac, ec = arow.factors[fname], erow.factors.get(fname)
            if ec is None:
                continue
            res.stats["compared"] += 1
            ctx: dict[str, Any] = dict(table_id=tid, table_name=tname, key=key_text(a, k), column=a_f[fname].source_header,
                       approved_ref=ref(a, ac), export_ref=ref(e, ec), approved_value=ac.raw or "", export_value=ec.raw or "")
            if ac.kind == "BLANK":
                res.findings.append(make_finding("BLANK_FACTOR", severity=Severity.HIGH, message="Approved factor is blank.", **ctx))
                continue
            if ec.kind == "BLANK":
                res.findings.append(make_finding("BLANK_FACTOR", severity=Severity.CRITICAL,
                                                 message="Export factor is blank; the approved value is missing.", **ctx))
                res.stats["mismatched"] += 1
                continue
            if "EXCEL_ERROR" in ac.flags:
                continue                      # reported as a BLOCKER by the integrity checks
            ad, ed = to_decimal_safe(ac.value), to_decimal_safe(ec.value)
            if ad is None or ed is None or "UNPARSEABLE" in ac.flags or "UNPARSEABLE" in ec.flags:
                if ac.value == ec.value:
                    res.stats["matched"] += 1
                else:
                    res.stats["mismatched"] += 1
                    res.findings.append(make_finding("VALUE_MISMATCH", severity=Severity.CRITICAL,
                                                     message=f"approved {ac.value} vs export {ec.value}", **ctx))
                continue
            col = e_f[fname]
            scale = col.scale if col.scale is not None else (0 if col.type.value == "INTEGER" else
                                                             int(ov.get("scale", cfg.comparison.default_scale)))
            rule, det = compare_values(ad, ed, scale, cfg, abs_tol)
            if rule == "MATCH":
                res.stats["matched"] += 1
                continue
            if rule in ("MATCH_ROUNDED", "PRECISION_TRUNCATION"):
                res.stats["rounded"] += 1
                res.stats["matched"] += 1
                if rule == "MATCH_ROUNDED":
                    rounded_emitted += 1
                    if rounded_emitted > MAX_ROUNDED_FINDINGS:
                        continue
                msg = f"approved {ac.value} rounds to {det['approved_rounded']} at scale {det['scale']}; export {ec.value}"
                res.findings.append(make_finding(rule, severity=_SEV[rule], message=msg, details=dict(det), **ctx))
                continue
            res.stats["mismatched"] += 1
            msg = f"approved {ac.value} vs export {ec.value} ({det.get('pct', '')})"
            res.findings.append(make_finding(rule, severity=_SEV[rule], message=msg, details=dict(det), **ctx))
    # extra rows
    a_keys = set(a_idx.rows) | struck
    for k in e_idx.rows:
        if k not in a_keys:
            res.extra.append(k)
        elif k in struck:
            r = e_idx.rows[k]
            res.findings.append(make_finding(
                "EXTRA_ROW_IN_EXPORT", severity=Severity.HIGH, table_id=tid, table_name=tname, key=key_text(e, k),
                export_ref=ref(e, r.key[0] if r.key else None),
                message="Row is struck through in the approved workbook (intended deletion) but still exists in the export."))
    wp, um, ue = _wildcard_pairs(res.missing, res.extra)
    for mk, ek in wp:
        ar, er = a_idx.rows[mk], e_idx.rows[ek]
        res.findings.append(make_finding(
            "WILDCARD_MISMATCH", severity=Severity.CRITICAL, table_id=tid, table_name=tname, key=key_text(a, mk),
            approved_value=key_text(a, mk), export_value=key_text(e, ek),
            approved_ref=ref(a, ar.key[0] if ar.key else None), export_ref=ref(e, er.key[0] if er.key else None),
            message=f"Approved row [{key_text(a, mk)}] vs export row [{key_text(e, ek)}]: wildcard vs concrete value."))
        res.stats["mismatched"] += 1
    res.missing = [k for k in res.missing if k not in um]
    res.extra = [k for k in res.extra if k not in ue]
    return res


def emit_row_findings(res: FactorResult, pair: TablePair) -> None:
    """Missing / extra row findings; called after boundary-shift detection removed explained rows."""
    a, e = pair.approved, pair.export
    assert a is not None and e is not None and res.a_idx is not None and res.e_idx is not None
    for k in res.missing:
        r: Row = res.a_idx.rows[k]
        res.findings.append(make_finding(
            "MISSING_ROW_IN_EXPORT", severity=Severity.HIGH, table_id=pair.id, table_name=pair.display, key=key_text(a, k),
            approved_value=", ".join(f"{n}={c.raw}" for n, c in r.factors.items()), approved_ref=ref(a, r.key[0] if r.key else None),
            message="Approved row not found in export."))
        res.stats["mismatched"] += 1
    for k in res.extra:
        r = res.e_idx.rows[k]
        res.findings.append(make_finding(
            "EXTRA_ROW_IN_EXPORT", severity=Severity.HIGH, table_id=pair.id, table_name=pair.display, key=key_text(e, k),
            export_value=", ".join(f"{n}={c.raw}" for n, c in r.factors.items()), export_ref=ref(e, r.key[0] if r.key else None),
            message="Export row not found in the approved (full) table."))
        res.stats["mismatched"] += 1
