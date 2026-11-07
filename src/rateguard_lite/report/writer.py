"""Report writers: verdict.txt, JSON, XLSX, HTML, Jira comment, scenarios, manifest (spec 13)."""

from __future__ import annotations

import csv
import json
import platform
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

import openpyxl
from jinja2 import Environment, FileSystemLoader, select_autoescape
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .. import __version__
from ..config import aux
from ..model.findings import GATE_TEXT, Finding, Verdict

if TYPE_CHECKING:
    from ..pipeline import RunResult

BLOCKING = ("BLOCKER", "CRITICAL", "HIGH", "NEEDS_MAPPING")
RANGE_RULES = {"RANGE_GAP", "RANGE_OVERLAP", "RANGE_MIN_GT_MAX", "OPEN_END_MISSING", "OFF_BY_ONE_BOUNDARY"}
META_RULES = {"EFFECTIVE_DATE_MISMATCH", "EFFECTIVE_DATE_NOT_EVALUATED", "SCOPE_METADATA_MISMATCH", "EDITION_NOT_INCREMENTED", "EDITION_MISMATCH"}
LEVEL_FILL = {"BLOCKER": "7F1D1D", "CRITICAL": "B42318", "HIGH": "DC6803", "NEEDS_MAPPING": "7A5AF8", "NEEDS_REVIEW": "7A5AF8",
              "NOT_EVALUATED": "667085", "WARNING": "B54708", "INFO": "475467"}


def ref_text(f: Finding) -> str:
    refs = [r for r in (f.approved_ref, f.export_ref) if r]
    return " | ".join(refs)


def line_for(f: Finding) -> str:
    tail = ""
    if f.approved_ref or f.export_ref:
        tail = f" ({ref_text(f)})"
    key = f"{f.key}: " if f.key else ""
    return f"[{f.level}] {f.table_name or f.table_id or '-'} — {f.rule_id}: {key}{f.message}{tail}"


def next_steps(findings: list[Finding]) -> list[str]:
    groups: dict[tuple[str, str], list[Finding]] = defaultdict(list)
    for f in findings:
        if f.level in BLOCKING + ("NEEDS_REVIEW",):
            groups[(f.rule_id, f.table_id)].append(f)
    steps = []
    for (rule, _), fs in sorted(groups.items(), key=lambda kv: kv[1][0].sort_key()):
        fx = fs[0].suggested_fix or aux("messages").get(rule, {}).get("fix", "")
        label = fs[0].table_name or fs[0].table_id
        steps.append(f"{fx} [{rule}{' in ' + label if label else ''}{f', {len(fs)} item(s)' if len(fs) > 1 else ''}]")
    return steps[:10]


def verdict_text(r: RunResult, width: int = 100) -> str:
    s = r.summary
    gate = GATE_TEXT[r.verdict]
    lines = [
        f"RATEGUARD-LITE VERDICT: {r.verdict.value.replace('_', ' ')} — {gate}",
        f"Ticket: {s['ticket'] or '-'} | LOB: {s['lob'] or '-'} | State: {','.join(s['states']) or '-'} | Filing effective: {s['filing_effective_date'] or '-'}",
        f"Rate book: {s['rate_book'] or '-'} | Export edition: {s['export_edition'] or '-'} | Baseline: {s['baseline']}",
        f"Tables: {s['tables_analyzed']} analyzed | {s['tables_matched']} matched | {s['tables_with_discrepancies']} with discrepancies | {s['scope_violations']} scope violation(s)",
        f"Cells: {s['cells_compared']:,} compared | {s['critical']} critical | {s['high']} high | {s['warnings']} warnings | {s['rounded_matches']} rounded matches",
    ]
    if r.tolerance_notes:
        lines.append("Tolerance overrides: " + "; ".join(r.tolerance_notes))
    block = [f for f in r.findings if f.level in BLOCKING]
    review = [f for f in r.findings if f.level in ("NEEDS_REVIEW", "NOT_EVALUATED")]
    if block:
        lines.append("BLOCKING ITEMS")
        lines += [" " + line_for(f) for f in block[:15]]
        if len(block) > 15:
            lines.append(f" ... and {len(block) - 15} more (see reconciliation_report.xlsx)")
    if review:
        lines.append("NEEDS REVIEW / NOT EVALUATED")
        lines += [" " + line_for(f) for f in review[:8]]
        if len(review) > 8:
            lines.append(f" ... and {len(review) - 8} more")
    steps = next_steps(r.findings)
    if steps:
        lines.append("NEXT STEPS")
        lines += [f" {i}. {t}" for i, t in enumerate(steps, 1)]
    elif r.verdict == Verdict.PASS:
        lines += ["NEXT STEPS", " 1. Proceed to UAT. Use test_scenarios.xlsx for the targeted QA script."]
    return "\n".join(lines) + "\n"


def jira_comment(r: RunResult) -> str:
    s = r.summary
    out = [f"RateGuard-Lite: {r.verdict.value.replace('_', ' ')} - {GATE_TEXT[r.verdict]}",
           f"Rate book {s['rate_book'] or '-'} edition {s['export_edition'] or '-'}, filing effective {s['filing_effective_date'] or '-'}.",
           f"Tables: {s['tables_analyzed']} analyzed, {s['tables_matched']} matched, {s['tables_with_discrepancies']} with discrepancies, "
           f"{s['scope_violations']} scope violation(s). Cells compared: {s['cells_compared']:,}.",
           f"Baseline: {s['baseline']}."]
    block = [f for f in r.findings if f.level in BLOCKING]
    if block:
        out.append("")
        out.append("Blocking items:")
        out += ["* " + f"{f.rule_id} - {f.table_name or f.table_id}: {f.key + ': ' if f.key else ''}{f.message}" for f in block[:10]]
        if len(block) > 10:
            out.append(f"* ... and {len(block) - 10} more")
    lim = [f for f in r.findings if f.level == "NOT_EVALUATED"]
    if lim:
        out += ["", "Not checked: " + "; ".join(f.message for f in lim[:2])]
    steps = next_steps(r.findings)
    if steps:
        out += ["", "Next steps:"] + [f"{i}. {t}" for i, t in enumerate(steps[:6], 1)]
    out += ["", f"Run {r.run_id}. Full detail is in the attached reports on the QA share."]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- xlsx
def _put(ws: Any, row: int, col: int, v: Any, bold: bool = False) -> None:
    c = ws.cell(row=row, column=col)
    c.value = v
    if isinstance(v, str):
        c.data_type = "s"           # never let user text become a formula
    if bold:
        c.font = Font(bold=True)


def _sheet(wb: openpyxl.Workbook, title: str, headers: list[str], rows: list[list[Any]], widths: list[int] | None = None,
           level_col: int | None = None) -> None:
    ws = wb.create_sheet(title)
    head_fill = PatternFill("solid", fgColor="0B1F3A")
    for j, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=j, value=h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = head_fill
        c.alignment = Alignment(vertical="center")
    for i, row in enumerate(rows, 2):
        for j, v in enumerate(row, 1):
            _put(ws, i, j, v)
            ws.cell(row=i, column=j).alignment = Alignment(vertical="top", wrap_text=j in (len(headers),))
        if level_col is not None:
            lvl = str(row[level_col - 1])
            if lvl in LEVEL_FILL:
                ws.cell(row=i, column=level_col).font = Font(bold=True, color="FFFFFF")
                ws.cell(row=i, column=level_col).fill = PatternFill("solid", fgColor=LEVEL_FILL[lvl])
    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(rows) + 1}"
    for j, w in enumerate(widths or [], 1):
        ws.column_dimensions[get_column_letter(j)].width = w


def _finding_row(f: Finding) -> list[Any]:
    return [f.level, f.rule_id, f.table_id, f.table_name, f.key, f.column, f.approved_value, f.export_value, f.approved_ref, f.export_ref,
            f.message, f.explanation, f.suggested_fix, f.details.get("hint", "")]


FIND_HEAD = ["Level", "Rule", "Table ID", "Table", "Key", "Column", "Approved", "Export", "Approved cell", "Export cell", "Detail", "What it means", "Suggested fix", "Hint"]
FIND_W = [14, 26, 14, 28, 30, 18, 14, 14, 34, 34, 60, 50, 50, 30]


def write_xlsx(r: RunResult, path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Summary"
    s = r.summary
    items = [("Verdict", f"{r.verdict.value} — {GATE_TEXT[r.verdict]}"), ("Run", r.run_id), ("Tool version", __version__), ("Config hash", r.cfg_hash),
             ("Ticket", s["ticket"]), ("LOB", s["lob"]), ("State", ",".join(s["states"])), ("Filing effective", s["filing_effective_date"]),
             ("Rate book", s["rate_book"]), ("Export edition", s["export_edition"]), ("Baseline", s["baseline"]),
             ("Tables analyzed", s["tables_analyzed"]), ("Tables matched", s["tables_matched"]), ("Tables with discrepancies", s["tables_with_discrepancies"]),
             ("Scope violations", s["scope_violations"]), ("Cells compared", s["cells_compared"]), ("Critical", s["critical"]), ("High", s["high"]),
             ("Blockers", s["blockers"]), ("Needs mapping", s["needs_mapping"]), ("Needs review", s["needs_review"]), ("Not evaluated", s["not_evaluated"]),
             ("Warnings", s["warnings"]), ("Rounded matches", s["rounded_matches"])]
    if r.tolerance_notes:
        items.append(("Tolerance overrides", "; ".join(r.tolerance_notes)))
    for i, (k, v) in enumerate(items, 1):
        _put(ws, i, 1, k, bold=True)
        _put(ws, i, 2, v)
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 90
    _sheet(wb, "Discrepancies", FIND_HEAD, [_finding_row(f) for f in r.findings if f.level != "INFO"], FIND_W, level_col=1)
    _sheet(wb, "Scope Audit", ["Table ID", "Table", "In Jira (action)", "In approved", "In export", "Changed vs baseline", "Mapping", "Status"],
           [[x["table_id"], x["table"], x["in_jira"], x["in_approved"], x["in_export"], x["changed_vs_baseline"], x["mapping"], x["status"]] for x in r.scope],
           [16, 34, 18, 12, 12, 20, 14, 28])
    _sheet(wb, "Range Integrity", FIND_HEAD, [_finding_row(f) for f in r.findings if f.rule_id in RANGE_RULES], FIND_W, level_col=1)
    _sheet(wb, "Metadata Audit", FIND_HEAD, [_finding_row(f) for f in r.findings if f.rule_id in META_RULES], FIND_W, level_col=1)
    rep = r.to_report()
    _sheet(wb, "Mapping", ["Table ID", "Table", "Approved", "Export", "Confidence", "Score", "Reason", "Jira action", "Jira mapping"],
           [[m["table_id"], m["table"], m["approved"] or "", m["export"] or "", m["confidence"], m["score"], m["reason"], m["jira_action"] or "", m["jira_confidence"]] for m in rep.mapping],
           [16, 34, 30, 34, 14, 8, 22, 14, 14])
    _sheet(wb, "Warnings", ["Type", "Message"], [["warning", w] for w in r.warnings] + [["info", i] for i in r.info] +
           [["info", f"Info finding {f.rule_id}: {f.message}"] for f in r.findings if f.level == "INFO" and f.rule_id not in ("MATCH_ROUNDED",)][:200], [12, 140])
    _sheet(wb, "Inputs", ["Role", "File", "SHA-256"], [[i.role, i.name, i.sha256] for i in r.inputs], [12, 40, 70])
    wb.save(path)


def write_scenarios(r: RunResult, out: Path) -> None:
    t = r.tests
    if t is None:
        return
    wb = openpyxl.Workbook()
    first = wb.active
    assert first is not None
    wb.remove(first)
    _sheet(wb, "Parameter Tests", ["Test ID", "Priority", "Objective", "Table", "Key parameters", "Expected factor", "Source cell", "What to check in the rating worksheet"],
           [[x["test_id"], x["priority"], x["objective"], x["table"], x["key_parameters"], x["expected_factor"], x["source_cell"], x["check"]] for x in t.parameter],
           [14, 9, 38, 34, 60, 34, 40, 70])
    _sheet(wb, "Transaction Matrix", ["Test ID", "Priority", "Transaction", "Policy effective date", "Expected rate book", "What to check"],
           [[x["test_id"], x["priority"], x["transaction"], x["policy_effective_date"], x["expected_rate_book"], x["what_to_check"]] for x in t.transaction],
           [10, 9, 56, 24, 40, 70])
    _sheet(wb, "Assumptions", ["Assumption"], [[a] for a in t.assumptions], [150])
    wb.save(out / "test_scenarios.xlsx")
    with (out / "test_scenarios.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["section", "test_id", "priority", "objective_or_transaction", "table_or_date", "parameters_or_expected_rate_book", "expected_factor", "source_cell", "check"])
        for x in t.parameter:
            w.writerow(["parameter", x["test_id"], x["priority"], x["objective"], x["table"], _csv_safe(x["key_parameters"]), _csv_safe(x["expected_factor"]), x["source_cell"], x["check"]])
        for x in t.transaction:
            w.writerow(["transaction", x["test_id"], x["priority"], x["transaction"], x["policy_effective_date"], x["expected_rate_book"], "", "", x["what_to_check"]])


def _csv_safe(v: str) -> str:
    return "'" + v if v[:1] in ("=", "+", "-", "@") else v


# ---------------------------------------------------------------- html
def write_html(r: RunResult, path: Path) -> None:
    env = Environment(loader=FileSystemLoader(str(Path(__file__).parent / "templates")), autoescape=select_autoescape(["html"]))
    tpl = env.get_template("report.html")
    s = r.summary
    levels = Counter(f.level for f in r.findings)
    rep = r.to_report()
    html = tpl.render(
        r=r, s=s, gate=GATE_TEXT[r.verdict], verdict=r.verdict.value, vclass=r.verdict.value.lower(), version=__version__,
        findings=[f for f in r.findings], levels=levels, steps=next_steps(r.findings), scope=r.scope, mapping=rep.mapping,
        inputs=r.inputs, tests=r.tests, warnings=r.warnings, info=r.info, ref_text=ref_text,
        level_order=["BLOCKER", "CRITICAL", "HIGH", "NEEDS_MAPPING", "NEEDS_REVIEW", "NOT_EVALUATED", "WARNING", "INFO"],
    )
    path.write_text(html, encoding="utf-8")


def write_manifest(r: RunResult, path: Path) -> None:
    m = {
        "tool_version": __version__, "python": sys.version.split()[0], "platform": platform.system(), "run_id": r.run_id, "started_at": r.started_at,
        "user": r.user, "config_hash": r.cfg_hash, "learned_store_version": r.learned_version, "learned_items_used": r.learned_used,
        "inputs": [i.model_dump() for i in r.inputs], "baseline": r.baseline_info, "timings_seconds": r.timings,
        "snapshot_id": r.snapshot_id, "profile_config": r.cfg.model_dump(),
    }
    path.write_text(json.dumps(m, indent=2, sort_keys=True), encoding="utf-8")


def write_all(r: RunResult, out: Path) -> None:
    out = Path(out)
    (out / "verdict.txt").write_text(verdict_text(r), encoding="utf-8")
    rep = r.to_report()
    (out / "reconciliation_report.json").write_text(json.dumps(rep.model_dump(mode="json"), indent=2, sort_keys=True), encoding="utf-8")
    write_xlsx(r, out / "reconciliation_report.xlsx")
    write_html(r, out / "reconciliation_report.html")
    write_scenarios(r, out)
    (out / "jira_comment.txt").write_text(jira_comment(r), encoding="utf-8")
    (out / "proposals.json").write_text(json.dumps(r.proposals, indent=2, sort_keys=True), encoding="utf-8")
    write_manifest(r, out / "run_manifest.json")
