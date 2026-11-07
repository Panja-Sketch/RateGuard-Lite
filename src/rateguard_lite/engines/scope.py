"""Engine C: scope audit - Jira vs approved vs export vs baseline (spec 8.3)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..ingest.jira import JiraScope
from ..ingest.result import IngestResult
from ..mapping.tables import MappingResult, TablePair
from ..model.findings import Finding, Severity, Status
from .common import make_finding

NON_UPDATE = {"BYPASS", "REMOVE", "MENTION", "CONFLICT"}


def _plain_reason(reason: str) -> str:
    """Turn a layout failure into a sentence a QA analyst can act on (D-027)."""
    r = reason.lower()
    if "empty" in r and "header" not in r:
        return "The tab has nothing in it."
    if "header" in r and ("could not find" in r or "empty" in r or "too low" in r or "plausible" in r):
        return "We couldn't find the column headers."
    if "data-type row" in r:
        return "We couldn't find the row that lists the column types (for example DECIMAL or STRING)."
    if "more than one" in r or "several tables" in r:
        return "More than one table was found for the same name."
    return "The layout of the tab is not what we expected."


def scope_findings(mapping: MappingResult, jira: JiraScope, approved_ing: IngestResult, export_ing: IngestResult) -> list[Finding]:
    out: list[Finding] = []
    for p in mapping.pairs:
        ctx: dict[str, Any] = dict(table_id=p.id, table_name=p.display)
        if p.approved is not None and p.export is None:
            if p.confidence == "UNMAPPED":
                cands = ", ".join(f"{c} ({s:.2f})" for c, s in p.candidates)
                out.append(make_finding("NEEDS_MAPPING", status=Status.NEEDS_MAPPING,
                                        message=f"Approved table '{p.approved.display_name}' could not be matched to an export table. Closest: {cands or 'none'}.", **ctx))
            else:
                out.append(make_finding("TABLE_MISSING_IN_EXPORT", severity=Severity.CRITICAL,
                                        message=f"Approved table '{p.approved.display_name}' has no counterpart in the Guidewire export.", **ctx))
        if p.approved is not None and p.export is not None and p.confidence == "SUGGESTED":
            out.append(make_finding("SUGGESTED_MAPPING", status=Status.NEEDS_REVIEW,
                                    message=f"'{p.approved.display_name}' was matched to export table '{p.export.display_name}' by similarity "
                                            f"({p.score:.2f}); confirm the mapping.", **ctx))
        act = p.jira_action
        if p.approved is not None and not p.jira and p.jira_action is None:
            out.append(make_finding("UNDOCUMENTED_APPROVED_TABLE", severity=Severity.WARNING,
                                    message=f"Approved table '{p.approved.display_name}' is not mentioned in the Jira story.", **ctx))
        if p.approved is None and p.jira and act in ("ADD", "MODIFY", "REPLACE"):
            out.append(make_finding("APPROVED_FILE_MISSING_TABLE", severity=Severity.HIGH,
                                    message=f"Jira declares '{act}' for this table but it is not in the approved workbook.", **ctx))
        if p.jira and p.jira_confidence == "SUGGESTED":
            out.append(make_finding("SUGGESTED_MAPPING", status=Status.NEEDS_REVIEW,
                                    message="The Jira reference to this table was matched by similarity; confirm the mapping.", **ctx))
        if act == "CONFLICT":
            lines = ", ".join(str(r.line_no) for r in p.jira)
            out.append(make_finding("JIRA_CONFLICT", status=Status.NEEDS_REVIEW,
                                    message=f"The Jira story gives conflicting actions for this table (lines {lines}).", **ctx))
    # Jira references that matched no table anywhere
    for r in mapping.resolved_jira:
        if r.canonical_id is None and r.action.value in ("ADD", "MODIFY", "REPLACE"):
            out.append(make_finding("APPROVED_FILE_MISSING_TABLE", severity=Severity.HIGH, table_id=r.table_number or (r.table_name or "?"),
                                    table_name=r.table_name or "",
                                    message=f"Jira line {r.line_no} declares '{r.action.value}' for a table found in neither the approved workbook nor the export: {r.raw_text[:80]}"))
    for flag in jira.review_flags:
        out.append(make_finding("JIRA_CONFLICT" if flag.startswith("JIRA_CONFLICT") else "JIRA_REVIEW", status=Status.NEEDS_REVIEW, message=flag))
    for sig in jira.unparsed_signals:
        out.append(make_finding("JIRA_UNPARSED", status=Status.NEEDS_REVIEW, message=sig))
    for ing, side in ((approved_ing, "approved"), (export_ing, "export")):
        for iss in ing.issues:
            if iss.status == "NEEDS_MAPPING":
                out.append(make_finding("NEEDS_MAPPING", status=Status.NEEDS_MAPPING, table_id=iss.sheet, table_name=iss.sheet,
                                        message=f"[{side}] {iss.file}!{iss.sheet}: {iss.reason}",
                                        plain_what=f"We couldn't read the table on tab '{iss.sheet}' in {iss.file}. {_plain_reason(iss.reason)}",
                                        plain_why="Until it can be read, none of its values are checked.",
                                        plain_action="Run `rgl inspect` on this file, or ask your admin to update the layout settings."))
            elif iss.status == "HIDDEN":
                out.append(make_finding("HIDDEN_SHEET", severity=Severity.WARNING, table_id=iss.sheet, table_name=iss.sheet,
                                        message=f"[{side}] {iss.file}!{iss.sheet}: {iss.reason}"))
    return out


def scope_matrix(pairs: list[TablePair], statuses: dict[str, str], deltas: Mapping[str, Any], findings: list[Finding]) -> list[dict[str, Any]]:
    worst: dict[str, str] = {}
    rank = {"BLOCKER": 0, "CRITICAL": 1, "HIGH": 2, "NEEDS_MAPPING": 3, "NEEDS_REVIEW": 4, "NOT_EVALUATED": 5, "WARNING": 6, "INFO": 7}
    for f in findings:
        if f.table_id and f.level in rank and rank[f.level] < rank.get(worst.get(f.table_id, "INFO"), 7) and f.level != "INFO":
            worst[f.table_id] = f.level
    rows = []
    for p in pairs:
        if p.approved is None and not p.jira and statuses.get(p.id, "UNCHANGED") == "UNCHANGED" and p.id not in worst:
            continue        # untouched, undeclared tables are summarized, not listed
        delta = deltas.get(p.id)
        rows.append({
            "table_id": p.id, "table": p.display,
            "in_jira": p.jira_action or "-", "in_approved": "yes" if p.approved else "no",
            "in_export": "yes" if p.export else "no",
            "changed_vs_baseline": ("n/a" if delta is None else ("yes" if not getattr(delta, "empty", True) else "no")),
            "mapping": p.confidence if p.approved else "-",
            "status": worst.get(p.id, statuses.get(p.id, "OK")),
        })
    return rows
