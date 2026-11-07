# Decisions log

Where the build specification (v1.1) was ambiguous or in tension with itself, the option that **fails closed** was chosen and recorded here.

| ID | Decision | Why |
|---|---|---|
| D-001 | Config lives in the repo-level `config/` folder, found via `RGL_CONFIG_DIR`, else by walking up from the package. Install with `pip install -e .` (the setup scripts do). | Spec 14/15 puts `config/` at the repo root. A non-editable wheel install would not find it. |
| D-002 | The canonical model (`CellValue`, `Row`, `RateTable`, ...) uses **frozen slotted dataclasses**, not pydantic. Pydantic remains for config, Jira scope, findings and the report. | These objects are built only by our own code; pydantic validation was ~25 µs/cell and dominated a 600k-cell run. |
| D-003 | Guidewire exports are read by a small **lxml** values-only reader (`ingest/fastxlsx.py`); openpyxl is used for approved workbooks (needs formatting). Entities, DTDs and network access are disabled. | openpyxl falls back to pure-Python `defusedxml` parsing (~46 µs/cell). Needed for the A6 targets. |
| D-004 | A table pair is **CONFIRMED** without a learned alias when identity is exact: same table number *and* name similarity >= 0.85, or identical normalized names with matching key columns and no conflicting number. Similarity-only matches (>= 0.80, margin >= 0.10) are **SUGGESTED** and cap the verdict at `NEEDS_REVIEW`. | Otherwise the very first run could never be `PASS`, contradicting A1/A3. Ties are never exact. |
| D-005 | Conflicting Jira actions for one table are **always** `NEEDS_REVIEW` (the comment-timestamp "latest wins" rule is not implemented). `JiraScope` has an extra field `review_flags`. | Fails closed. Pasted Jira exports rarely carry comment timestamps. |
| D-006 | `MISSING_UPDATE` is raised only when the export is unchanged vs the baseline **and** the approved table does not equal the export. If approved = export = baseline for a declared update, it is an INFO `APPROVED_ROW_UNCHANGED`. | Approved tables are FULL (spec 2, 6.4), so "export equals approved" means the intended state is in place. |
| D-007 | An approved table with no confident match is `NEEDS_MAPPING` (BLOCKED) when its best candidate scores >= 0.20; below that it is `TABLE_MISSING_IN_EXPORT` (CRITICAL). | Cannot tell "not loaded" from "named differently" without a plausible candidate. |
| D-008 | In a Guidewire export, an **interior fully blank row is data** (wildcard keys, blank factor). | This is exactly the "blanks loaded instead of wildcards" production defect. Approved workbooks may have gaps, which are skipped. |
| D-009 | The weak `_123_` table-number pattern is used for scoring only, never for table identity. | A filler code such as `BOP_Roof_001_Ext` would otherwise collide with real table 1. |
| D-010 | `python-dateutil` is not used; dates are parsed by a strict own parser. | dateutil guesses; the spec says ambiguous dates must be `NEEDS_REVIEW`. |
| D-011 | Effective date: book-level (majority of table metadata) must equal the Jira filing date; table-level only for approved/declared tables. | A new rate book edition shares one effective date; unchanged tables are not expected to differ. |
| D-012 | Engine D (ranges) and export-side integrity checks run on approved tables, their export counterparts, and export tables changed vs baseline. Unrelated legacy tables are not audited. | Pre-existing defects in untouched tables should not fail an unrelated change. |
| D-013 | `OPEN_END_MISSING` is raised only when the last band's max is a true BLANK that is not enabled as a wildcard. | Blank max is open-ended by default (wildcard); the rule covers the case where it is not. |
| D-014 | Tables with Jira action BYPASS are excluded from Engine A; they are checked by Engine B (`BYPASS_VIOLATION`). | A bypassed table's approved rows are not the target state. |
| D-015 | Every run stores an `UNVERIFIED` snapshot. `rgl snapshot take` records a `VERIFIED` one (explicit user action). A snapshot from a FAIL/BLOCKED run cannot be accepted. | Spec 8.7: only user-confirmed passing exports become baselines. |
| D-016 | Identical duplicate keys in an **export** are a WARNING; any duplicate in an approved table, or duplicates with different values, are a BLOCKER. | Literal reading of spec 5.7. |
| D-017 | Input-file warnings (external links, OLE) become `INPUT_WARNING` findings, so they cap the verdict at `PASS_WITH_WARNINGS`. Macro presence is INFO only. | Spec 5.1. |
| D-018 | `MATCH_ROUNDED` findings are capped at 500 per table (the count is still exact in the summary). | Keeps reports usable when a whole table is rounded. |
| D-019 | Pair IDs follow the approved table's identity (`{LOB}:{number}` or slug); export identity is used for export-only tables. | Stable across runs for the same workbook. |
| D-020 | Learned **layout fingerprints** are stored but not yet applied to header detection; `rgl map` resolves table mappings and hidden sheets. New layouts are calibrated via `config/gw_layouts.yaml`. | Time-boxed; the detection never guesses, so the gap is safe (NEEDS_MAPPING). |
| D-021 | The optional local-LLM Jira assist is not implemented (config flag exists, default off, loopback-only by validation intent). | Out of scope for the core; default is off. |
| D-022 | The fixture generator lives in `src/rateguard_lite/samples/generator.py`; `tests/fixtures/generator.py` re-exports it. | `rgl sample` and `rgl selftest` ship with the package. |
| D-023 | Large fixture tables: "large" (10k-30k rows) tables are `max(1, tables // 60)` per workbook, so a 120-table export has ~160k cells, a 220-table export ~330k. | Keeps fixture generation fast; the performance targets are met with margin (220 tables ~10 s, ~470 MB). |
