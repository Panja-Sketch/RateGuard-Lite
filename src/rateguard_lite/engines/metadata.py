"""Engine E: metadata / edition / effective date audit (spec 8.5)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..config import Config
from ..ingest.jira import JiraScope
from ..mapping.tables import TablePair
from ..model.core import RateBook
from ..model.findings import Finding, Severity, Status
from .common import make_finding


def audit_metadata(export: RateBook, approved: RateBook | None, baseline_meta: dict[str, str] | None, jira: JiraScope,
                   pairs: list[TablePair], cfg: Config, has_changes: bool) -> list[Finding]:
    out: list[Finding] = []
    em = export.metadata
    fd = jira.filing_effective_date
    ed = em.get("effective_date")
    if fd is None:
        out.append(make_finding("EFFECTIVE_DATE_NOT_EVALUATED", status=Status.NOT_EVALUATED,
                                message="The Jira story has no filing effective date, so the export date was not checked.",
                                plain_what="The story has no filing effective date, so we could not check the rate book date.",
                                plain_action="Add 'Filing Effective Date: MM/DD/YYYY' to the story, then run again."))
    elif ed is None:
        out.append(make_finding("EFFECTIVE_DATE_NOT_EVALUATED", status=Status.NOT_EVALUATED,
                                message="The export has no readable effective date in its metadata block.",
                                plain_what="We could not find an effective date in the PolicyCenter export, so we could not check the rate book date.",
                                plain_action="Run `rgl inspect` on the export, or ask your admin to update the layout settings."))
    elif ed != fd:
        out.append(make_finding("EFFECTIVE_DATE_MISMATCH", severity=Severity.CRITICAL, approved_value=fd, export_value=ed,
                                message=f"Rate book effective date {ed} does not match the Jira filing effective date {fd}.",
                                plain_what=f"The rate book in PolicyCenter starts on {ed}, but the story says the filing starts on {fd}."))
    if fd is not None:
        for p in pairs:
            if p.export is None or not (p.approved is not None or p.jira_action in ("ADD", "MODIFY", "REPLACE")):
                continue
            td = p.export.metadata.get("effective_date")
            if td and td != fd and td != ed:
                out.append(make_finding("EFFECTIVE_DATE_MISMATCH", severity=Severity.CRITICAL, table_id=p.id, table_name=p.display,
                                        approved_value=fd, export_value=td, export_ref=f"{p.export.source.file}!{p.export.source.sheet}",
                                        message=f"Table effective date {td} does not match the filing effective date {fd}.",
                                        plain_what=f"Table '{p.display}' starts on {td}, but the story says the filing starts on {fd}."))
    book_state_wrong = bool(jira.states and em.get("jurisdiction") and em["jurisdiction"].upper() not in jira.states)
    if book_state_wrong:
        out.append(make_finding("SCOPE_METADATA_MISMATCH", severity=Severity.HIGH, approved_value=",".join(jira.states),
                                export_value=em["jurisdiction"], message=f"Export jurisdiction {em['jurisdiction']} is not in the Jira state(s) {','.join(jira.states)}."))
    elif jira.states:
        # D-029: one finding per table whose own state label differs from the rate book's state (a book-level vote hides a lone odd table)
        shown = "/".join(jira.states)
        for p in pairs:
            if p.export is None:
                continue
            label = (p.export.metadata.get("jurisdiction") or "").strip()
            if label and label.upper() not in jira.states:
                out.append(make_finding(
                    "SCOPE_METADATA_MISMATCH", severity=Severity.HIGH, table_id=p.id, table_name=p.display, approved_value=shown, export_value=label,
                    export_ref=f"{p.export.source.file}!{p.export.source.sheet}",
                    message=f"Table jurisdiction {label} is not in the Jira state(s) {shown}.",
                    plain_what=f"This table is labelled {label} but this is the {shown} rate book.",
                    plain_why="Its values may belong to another state, so policies in this state could be rated with the wrong numbers.",
                    plain_action=f"Check which state's values were loaded into '{p.display}', fix the table, and export again."))
    if jira.lob and em.get("lob") and em["lob"].upper() != jira.lob.upper():
        out.append(make_finding("SCOPE_METADATA_MISMATCH", severity=Severity.HIGH, approved_value=jira.lob, export_value=em["lob"],
                                message=f"Export line of business {em['lob']} differs from Jira {jira.lob}."))
    if jira.edition and em.get("edition") and jira.edition.strip().upper() != em["edition"].strip().upper():
        out.append(make_finding("EDITION_MISMATCH", severity=Severity.HIGH, approved_value=jira.edition, export_value=em["edition"],
                                message=f"Export edition {em['edition']} differs from the edition {jira.edition} named in Jira."))
    if baseline_meta and has_changes and baseline_meta.get("edition") and em.get("edition") and \
            baseline_meta["edition"].strip().upper() == em["edition"].strip().upper():
        sev = Severity(cfg.metadata.edition_not_incremented_severity)
        out.append(make_finding("EDITION_NOT_INCREMENTED", severity=sev, approved_value=baseline_meta["edition"], export_value=em["edition"],
                                message=f"Export edition {em['edition']} equals the baseline edition although changes are declared."))
    if approved is not None and fd is not None:
        ad = approved.metadata.get("effective_date")
        if ad and ad != fd:
            out.append(make_finding("EFFECTIVE_DATE_MISMATCH", severity=Severity.WARNING, approved_value=ad, export_value=fd,
                                    message=f"The approved workbook's info sheet shows effective date {ad}, not the Jira filing date {fd}.",
                                    plain_what=f"The approved workbook shows an effective date of {ad}, but the story says {fd}."))
    return out


def out_of_scope_dated(pairs: list[TablePair], export: RateBook, jira: JiraScope, deltas: Mapping[str, Any] | None = None,
                       max_share: float = 0.30) -> tuple[list[Finding], list[str]]:
    """OUT_OF_SCOPE_TABLE_DATED (D-024): an export table that is neither in the approved file nor in Jira, yet carries the
    filing effective date or the export's new edition in its own metadata, may have been touched or loaded by mistake.

    Returns (findings, notes). Notes go to the run manifest. If a stamp is absent everywhere, or carried by more than `max_share`
    of the tables (D-031: a stamp that common says nothing about an individual table), that stamp is skipped.
    With a baseline (`deltas` given) only tables that really changed against it are flagged (D-031).
    """
    notes: list[str] = []
    stamps = {"effective_date": jira.filing_effective_date, "edition": (export.metadata.get("edition") or "").strip().upper() or None}
    per_table: dict[str, dict[str, str]] = {}
    for p in pairs:
        if p.export is None:
            continue
        per_table[p.id] = {k: (p.export.metadata.get(k) or "").strip() for k in stamps}
    active: list[str] = []
    for k, target in stamps.items():
        values = [v[k] for v in per_table.values() if v[k]]
        if not values:
            notes.append(f"{k}: the export has no per-table values; check skipped.")
        elif target is None:
            notes.append(f"{k}: no reference value (Jira filing date / export edition); check skipped.")
        elif len(set(values)) == 1:
            notes.append(f"{k}: every table carries the same value, so it cannot single out a table; check skipped.")
        elif sum(1 for v in values if (v.upper() if k == "edition" else v) == target) > max_share * len(values):
            share = sum(1 for v in values if (v.upper() if k == "edition" else v) == target) / len(values)
            notes.append(f"{k}: {share:.0%} of the tables carry the filing's value, so it cannot single out a table; check skipped.")
        else:
            active.append(k)
    out: list[Finding] = []
    if not active:
        return out, notes
    for p in pairs:
        if p.export is None or p.approved is not None or p.jira:
            continue
        if deltas is not None:
            d = deltas.get(p.id)
            if d is not None and getattr(d, "empty", False):
                continue                  # the baseline shows this table did not change
        hits = []
        for k in active:
            v, target = per_table[p.id][k], stamps[k]
            if v and target and (v.upper() if k == "edition" else v) == target:
                hits.append(("effective date" if k == "effective_date" else "edition", v))
        if hits:
            what = " and ".join(f"{n} {v}" for n, v in hits)
            out.append(make_finding(
                "OUT_OF_SCOPE_TABLE_DATED", status=Status.NEEDS_REVIEW, table_id=p.id, table_name=p.display,
                export_value="; ".join(v for _, v in hits), export_ref=f"{p.export.source.file}!{p.export.source.sheet}",
                message=f"This table is not in the approved workbook or the Jira story, but its own metadata shows {what}, the same as this filing."))
    notes.append(f"checked {len(per_table)} export table(s) on: {', '.join(active)}; {len(out)} flagged.")
    return out, notes
