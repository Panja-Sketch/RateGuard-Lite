"""Table-level integrity checks: duplicates, Excel errors, formulas, formatting flags (spec 5.5, 5.7)."""

from __future__ import annotations

from collections import Counter

from ..config import Config
from ..model.core import RateTable
from ..model.findings import Finding, Severity, Status
from .common import build_index, key_text, make_finding, ref

_CAP = 50


def integrity_findings(t: RateTable, pair_id: str, display: str, side: str, cfg: Config) -> list[Finding]:
    out: list[Finding] = []
    idx = build_index(t)
    for k, first, dup in idx.duplicates[:_CAP]:
        same = all(first.factors[n].value == dup.factors[n].value for n in first.factors)
        out.append(make_finding(
            "DUPLICATE_KEY", severity=Severity.BLOCKER if not same or side == "approved" else Severity.WARNING,
            table_id=pair_id, table_name=display, key=key_text(t, k),
            approved_ref=ref(t, dup.key[0] if dup.key else None) if side == "approved" else "",
            export_ref=ref(t, dup.key[0] if dup.key else None) if side == "export" else "",
            message=f"[{side}] rows {first.source_row} and {dup.source_row} share the same key"
                    + (" and the same values (identical duplicate)." if same else " but hold different values.")))
    counts: Counter[str] = Counter()
    firsts: dict[str, str] = {}
    emitted: Counter[str] = Counter()
    for r in t.rows:
        cells = list(r.key) + list(r.factors.values())
        for c in cells:
            for fl in c.flags:
                base = fl.split(":")[0]
                counts[base] += 1
                firsts.setdefault(base, c.coord)
                if base in ("EXCEL_ERROR", "FORMULA_NO_VALUE") and emitted[base] < _CAP:
                    emitted[base] += 1
                    out.append(make_finding(
                        base, severity=Severity.BLOCKER, table_id=pair_id, table_name=display, key=f"row {r.source_row}",
                        approved_ref=ref(t, c), approved_value=c.raw or "",
                        message=f"[{side}] cell {c.coord} holds {'an Excel error' if base == 'EXCEL_ERROR' else 'a formula with no cached value'}."))
                elif base == "AMBIGUOUS_DATE" and emitted[base] < _CAP:
                    emitted[base] += 1
                    out.append(make_finding("AMBIGUOUS_DATE", status=Status.NEEDS_REVIEW, table_id=pair_id, table_name=display,
                                            approved_ref=ref(t, c), message=f"[{side}] date '{c.raw}' at {c.coord} cannot be resolved unambiguously.",
                                            explanation="The date could be read in more than one way.", suggested_fix="Use an unambiguous date (YYYY-MM-DD)."))
                elif base == "UNPARSEABLE" and emitted[base] < _CAP:
                    emitted[base] += 1
                    out.append(make_finding("UNPARSEABLE_VALUE", status=Status.NEEDS_REVIEW, table_id=pair_id, table_name=display,
                                            approved_ref=ref(t, c), message=f"[{side}] value '{c.raw}' at {c.coord} could not be read as the expected type.",
                                            explanation="A cell holds text where a number or boolean was expected.", suggested_fix="Correct the cell in the source file."))
    # strikethrough rows (distinct rows, not cells)
    if side == "approved":
        struck_rows = [r for r in t.rows if r.key and any("STRIKETHROUGH" in c.flags for c in r.key)]
        if not struck_rows:
            struck_rows = [r for r in t.rows if any("STRIKETHROUGH" in c.flags for c in r.factors.values())]
        if struck_rows and cfg.approved.strikethrough_means == "review":
            out.append(make_finding("STRIKETHROUGH_ROW", status=Status.NEEDS_REVIEW, table_id=pair_id, table_name=display,
                                    approved_ref=f"{t.source.file}!{t.source.sheet}!row {struck_rows[0].source_row}",
                                    message=f"{len(struck_rows)} struck-through row(s) in the approved workbook (first: row {struck_rows[0].source_row})."))
        elif struck_rows:
            out.append(make_finding("STRIKETHROUGH_ROW", severity=Severity.INFO, table_id=pair_id, table_name=display,
                                    message=f"{len(struck_rows)} struck-through row(s) treated as intended deletions."))
    agg = {
        "HIDDEN_ROW": ("WARNING", "hidden row(s) in the data region were included"),
        "HIDDEN_COL": ("WARNING", "hidden column(s) in the data region were included"),
        "MERGED_KEY_FILLED": ("WARNING", "merged key cell(s) were filled down"),
        "POSSIBLE_LEADING_ZERO_LOSS": ("WARNING", "numeric code cell(s) in a text column may have lost leading zeros"),
        "DATE_HAS_TIME": ("WARNING", "date cell(s) carry a time component"),
        "FORMULA_CACHED": ("INFO", "formula cell(s) were read from cached values"),
        "PERCENT_SIGN_IGNORED": ("WARNING", "percent sign(s) ignored (column is not configured as percent)"),
    }
    for fl, (sev, text) in agg.items():
        if counts[fl]:
            out.append(make_finding(fl, severity=Severity(sev), table_id=pair_id, table_name=display,
                                    approved_ref=f"{t.source.file}!{t.source.sheet}!{firsts[fl]}",
                                    message=f"[{side}] {counts[fl]} {text} (first at {firsts[fl]}).",
                                    explanation=text.capitalize() + ".", suggested_fix="Check the source cells and confirm the values are intended."))
    if "MERGED" in counts and "MERGED_KEY_FILLED" not in counts:
        out.append(make_finding("MERGED_FACTOR", status=Status.NEEDS_REVIEW, table_id=pair_id, table_name=display,
                                message=f"[{side}] merged cells in the data region (factor cells)."))
    return out


def completeness_findings(approved: RateTable, export: RateTable, pair_id: str, display: str, cfg: Config) -> list[Finding]:
    if export.rows and len(approved.rows) < cfg.completeness.small_table_ratio * len(export.rows):
        return [make_finding(
            "APPROVED_TABLE_SUSPICIOUSLY_SMALL", status=Status.NEEDS_REVIEW, table_id=pair_id, table_name=display,
            approved_value=str(len(approved.rows)), export_value=str(len(export.rows)),
            message=f"Approved table has {len(approved.rows)} rows but the export has {len(export.rows)}; it may be a partial paste or a wrong mapping.")]
    return []
