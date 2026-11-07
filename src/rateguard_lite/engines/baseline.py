"""Engine B: baseline delta (spec 8.2). Runs only when a baseline exists (--baseline or Snapshot History)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..mapping.tables import TablePair
from ..model.core import RateTable
from ..model.findings import Finding, Severity
from .common import KeyT, build_index, key_names, key_text, make_finding, table_hash
from .factors import FactorResult


@dataclass
class Delta:
    added: list[KeyT] = field(default_factory=list)
    removed: list[KeyT] = field(default_factory=list)
    changed: list[KeyT] = field(default_factory=list)
    structural: bool = False

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed or self.structural)

    def size(self) -> int:
        return len(self.added) + len(self.removed) + len(self.changed)


def is_stub(t: RateTable | None) -> bool:
    return t is not None and t.metadata.get("stub") == "1"


def table_delta(base: RateTable, cur: RateTable) -> Delta:
    if is_stub(base):
        return Delta()
    if key_names(base) != key_names(cur) or sorted(c.canonical_name for c in base.factor_columns()) != sorted(
            c.canonical_name for c in cur.factor_columns()):
        return Delta(structural=True)
    bh = base.metadata.get("hash") or table_hash(base)
    if bh == table_hash(cur):
        return Delta()
    bi, ci = build_index(base), build_index(cur)
    d = Delta()
    for k, r in ci.rows.items():
        b = bi.rows.get(k)
        if b is None:
            d.added.append(k)
        elif any((r.factors[n].value, r.factors[n].kind) != (b.factors[n].value, b.factors[n].kind) for n in r.factors if n in b.factors):
            d.changed.append(k)
    d.removed = [k for k in bi.rows if k not in ci.rows]
    return d


def run_baseline(pairs: list[TablePair], factor_results: dict[str, FactorResult],
                 baseline_label: str) -> tuple[list[Finding], dict[str, Delta], dict[str, str]]:
    findings: list[Finding] = []
    deltas: dict[str, Delta] = {}
    status: dict[str, str] = {}
    declared_update = {"ADD", "MODIFY", "REPLACE"}
    for p in pairs:
        act = p.jira_action
        in_approved = p.approved is not None
        ctx: dict[str, Any] = dict(table_id=p.id, table_name=p.display)
        if p.export is not None and p.baseline is not None:
            d = table_delta(p.baseline, p.export)
        elif p.export is not None:
            d = Delta(added=list(build_index(p.export).rows))              # table did not exist before
        elif p.baseline is not None:
            d = Delta(removed=list(build_index(p.baseline).rows))
        else:
            continue
        deltas[p.id] = d
        fr = factor_results.get(p.id)
        approved_equals_export = bool(fr is not None and not fr.missing and not fr.extra and fr.stats["mismatched"] == 0
                                      and not any(f.level in ("CRITICAL", "HIGH", "NEEDS_MAPPING") for f in fr.findings))
        if act == "CONFLICT":
            status[p.id] = "CONFLICT"
            continue
        if d.empty:
            if act in declared_update:
                if in_approved and approved_equals_export:
                    findings.append(make_finding(
                        "APPROVED_ROW_UNCHANGED", severity=Severity.INFO, message=f"Declared '{act}' but the approved table equals the baseline "
                        f"({baseline_label}); all {len(p.approved.rows) if p.approved else 0} rows are unchanged.", **ctx))
                    status[p.id] = "UNCHANGED_DECLARED_NO_DIFF"
                else:
                    findings.append(make_finding("MISSING_UPDATE", severity=Severity.CRITICAL,
                                                 message=f"Jira declares '{act}' but the table is unchanged versus the baseline ({baseline_label}).", **ctx))
                    status[p.id] = "UNCHANGED_DECLARED"
            elif act == "REMOVE":
                findings.append(make_finding("REMOVE_NOT_APPLIED", severity=Severity.HIGH,
                                             message="Jira says to remove this table but it still exists, unchanged.", **ctx))
                status[p.id] = "REMOVE_NOT_APPLIED"
            else:
                status[p.id] = "UNCHANGED"
            continue
        n = d.size()
        detail = f"{len(d.added)} added, {len(d.removed)} removed, {len(d.changed)} changed row(s)" + (" (structure changed)" if d.structural else "")
        if act == "BYPASS":
            line = p.jira[0].line_no if p.jira else "?"
            findings.append(make_finding("BYPASS_VIOLATION", severity=Severity.CRITICAL, details={"rows_changed": n},
                                         message=f"Marked bypass in Jira line {line}; {detail} versus the baseline ({baseline_label}).", **ctx))
            status[p.id] = "BYPASS_VIOLATION"
        elif act in declared_update or act in ("REMOVE", "MENTION") or in_approved:
            status[p.id] = "CHANGED_DECLARED" if act else "CHANGED_APPROVED_ONLY"
        else:
            sample = ""
            ref_t = p.export or p.baseline
            if ref_t is not None:
                ks = (d.changed + d.added + d.removed)[:1]
                sample = f" e.g. {key_text(ref_t, ks[0])}" if ks else ""
            findings.append(make_finding("UNDECLARED_CHANGE", severity=Severity.CRITICAL, details={"rows_changed": n},
                                         message=f"Not in the Jira story or approved workbook, but changed versus the baseline ({baseline_label}): {detail}.{sample}", **ctx))
            status[p.id] = "UNDECLARED_CHANGE"
    return findings, deltas, status
