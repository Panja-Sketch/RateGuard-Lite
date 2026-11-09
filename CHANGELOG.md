# Changelog

Decision numbers (D-0xx) refer to [docs/DECISIONS.md](docs/DECISIONS.md).

## 1.1.0 - 2026-11-08

Verification of the first scenario-lab run, noise reduction and release documentation. No change to the finding rules, verdict logic or exit codes; new finding ids and report keys are additive.

### Added
- **Grouped problems and likely causes (D-030).** Every problem is grouped per table with a count and a plain headline ("3 tables have problems (98 cells)"). Likely cause is named only on strong evidence: rows written differently, label typo, values copied from another table, columns swapped, change not loaded, whole table differs; otherwise "values differ". Shown in `verdict.txt`, the Jira comment, the HTML tab "Problems by table", an Excel sheet and the JSON key `problems`. Cell-level findings are unchanged.
- **Table identity (D-029).** Messy tab names ("TAB 701 - ...", "Copy of 703 ...", "(2)", "FINAL", dates) are matched; the table number beats a same-named table; one finding per table with a different state label; one unmatched tab is one finding, not three.
- **`BYPASS_NOT_CHECKED` (D-031).** Without a baseline, a story with DO NOT LOAD tables gets one note (INFO; verdict unchanged).
- `rgl --version` (also `rgl version`).
- **Scenario Lab** (`scenario_lab/`): 19 synthetic cases (happy path, 10 planted failures, edge cases) with a coverage report; `python scenario_lab/run_lab.py`.
- `docs/INSTALL.md`, `docs/FIRST_REAL_RUN.md`, `scripts/build_wheelhouse.ps1` (offline install), `scripts/make_demo.py` and the demo page `docs/demo/index.html`.

### Changed
- **Less noise (D-031).** Leading-zero warning not raised for plain integers; a formula without a saved value and the hidden-sheet warning are reported once; a header-only approved tab is one clear input error (exit 3).
- **`OUT_OF_SCOPE_TABLE_DATED` is off by default (D-032).** New setting `scope.oos_enabled`; with a baseline only changed tables count and a stamp shared by more than `scope.oos_max_stamp_share` (30%) of tables is skipped (D-031). The profile `oos_stamp_wide` turns it on, so the self-test still exercises it. With a baseline, undeclared changes are caught by the baseline comparison.
- Version 1.1.0; `.gitignore` extended (`*.xls`, `*.db`, `scenario_lab/out/`, `wheelhouse/`, secrets).

### Known limitations
- Without a before-export, changes to DO NOT LOAD / deprecated / untargeted tables are not detected (noted once in the report).
- The local UI still lists findings flat; the grouped view is in the HTML/Excel/JSON/text outputs.
- Learned layout fingerprints are not applied (D-020); the optional local-LLM Jira assist is not implemented (D-021).

## 1.0.0

First release (D-001..D-028): ingestion of approved workbooks, Guidewire exports and Jira stories; engines for factor comparison, ranges, integrity, scope and metadata; verdicts and exit codes; HTML/Excel/JSON reports; Must Test list and plain-language findings (D-026, D-027, D-028); optional Snapshot History (D-024); learning that never changes comparison math; CLI and local UI.
