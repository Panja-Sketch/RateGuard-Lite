"""Engine E: metadata / edition / effective date audit (spec 8.5)."""

from __future__ import annotations

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
                                explanation="Without the filing date the rate book date cannot be verified.",
                                suggested_fix="Add 'Filing Effective Date: MM/DD/YYYY' to the story."))
    elif ed is None:
        out.append(make_finding("EFFECTIVE_DATE_NOT_EVALUATED", status=Status.NOT_EVALUATED,
                                message="The export has no readable effective date in its metadata block.",
                                suggested_fix="Run `rgl inspect` on the export and check the layout profile."))
    elif ed != fd:
        out.append(make_finding("EFFECTIVE_DATE_MISMATCH", severity=Severity.CRITICAL, approved_value=fd, export_value=ed,
                                message=f"Rate book effective date {ed} does not match the Jira filing effective date {fd}."))
    if fd is not None:
        for p in pairs:
            if p.export is None or not (p.approved is not None or p.jira_action in ("ADD", "MODIFY", "REPLACE")):
                continue
            td = p.export.metadata.get("effective_date")
            if td and td != fd and td != ed:
                out.append(make_finding("EFFECTIVE_DATE_MISMATCH", severity=Severity.CRITICAL, table_id=p.id, table_name=p.display,
                                        approved_value=fd, export_value=td, export_ref=f"{p.export.source.file}!{p.export.source.sheet}",
                                        message=f"Table effective date {td} does not match the filing effective date {fd}."))
    if jira.states and em.get("jurisdiction") and em["jurisdiction"].upper() not in jira.states:
        out.append(make_finding("SCOPE_METADATA_MISMATCH", severity=Severity.HIGH, approved_value=",".join(jira.states),
                                export_value=em["jurisdiction"], message=f"Export jurisdiction {em['jurisdiction']} is not in the Jira state(s) {','.join(jira.states)}."))
    if jira.lob and em.get("lob") and em["lob"].upper() != jira.lob.upper():
        out.append(make_finding("SCOPE_METADATA_MISMATCH", severity=Severity.HIGH, approved_value=jira.lob, export_value=em["lob"],
                                message=f"Export line of business {em['lob']} differs from Jira {jira.lob}."))
    if jira.edition and em.get("edition") and jira.edition.strip().upper() != em["edition"].strip().upper():
        out.append(make_finding("EDITION_MISMATCH", severity=Severity.HIGH, approved_value=jira.edition, export_value=em["edition"],
                                message=f"Export edition {em['edition']} differs from the edition {jira.edition} named in Jira.",
                                explanation="The exported rate book edition is not the one the story asks for.",
                                suggested_fix="Export the correct edition or correct the story."))
    if baseline_meta and has_changes and baseline_meta.get("edition") and em.get("edition") and \
            baseline_meta["edition"].strip().upper() == em["edition"].strip().upper():
        sev = Severity(cfg.metadata.edition_not_incremented_severity)
        out.append(make_finding("EDITION_NOT_INCREMENTED", severity=sev, approved_value=baseline_meta["edition"], export_value=em["edition"],
                                message=f"Export edition {em['edition']} equals the baseline edition although changes are declared."))
    if approved is not None and fd is not None:
        ad = approved.metadata.get("effective_date")
        if ad and ad != fd:
            out.append(make_finding("EFFECTIVE_DATE_MISMATCH", severity=Severity.WARNING, approved_value=ad, export_value=fd,
                                    message=f"The approved workbook's info sheet shows effective date {ad}, not the Jira filing date {fd}."))
    return out
