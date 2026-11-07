# Admin guide

## Install

Python 3.11+ (3.12 recommended). No admin rights needed.

```powershell
.\setup.ps1     # Windows
./setup.sh      # macOS / Linux
```

Both create `.venv`, run `pip install -e .` (editable; the tool reads `config/` from the repo) and `rgl selftest`.

## Configuration (`config/`)

| File | Purpose |
|---|---|
| `default.yaml` | Tolerances, limits, defaults. Unknown keys are a startup error. |
| `profiles/<name>.yaml` | Team/LOB overrides merged over `default.yaml`; select with `--profile`. |
| `gw_layouts.yaml` | Guidewire export layout: metadata labels, type tokens, factor markers, header position. |
| `synonyms.yaml` | Column synonyms, table-name abbreviations, LOB synonyms, range suffix pairs. |
| `wildcards.yaml` | Global wildcard tokens and per-table/column additions (e.g. `-1`). |
| `jira_patterns.yaml` | Action keywords, section headings, date labels. |
| `field_map.yaml` | Canonical column to PolicyCenter UI label, used in QA scenarios. |
| `messages.yaml` | Plain-language explanation and fix per finding rule. |
| `tests.yaml` | Transaction matrix templates, mid-term rate book rule. |

Env overrides: `RGL_CONFIG_DIR`, `RGL_DATA_DIR`.

### Key settings

- `comparison.default_scale` (4): decimals assumed when Guidewire does not declare a scale.
- `comparison.precision_materiality` (0.0005): relative change above which rounding becomes a `PRECISION_TRUNCATION` warning.
- `comparison.magnitude_ratio` (5): ratio at which a mismatch is `ORDER_OF_MAGNITUDE`.
- `comparison.abs_tolerance` (0) and `table_overrides`: any non-zero tolerance is **printed in the report header**.
- `approved.strikethrough_means`: `review` (default) or `delete`.
- `limits.*`: file size, unzipped size, cells (hard maxima 500 MB / 10M cells).
- `metadata.edition_not_incremented_severity`: `WARNING` or `HIGH`.

### Calibrating a new Guidewire layout

1. `rgl inspect Export.xlsx`
2. If the type row/header is not found, edit `gw_layouts.yaml` (`type_tokens`, `header_position`, `metadata_labels`).
3. Re-run `rgl inspect` until the columns and roles look right.

Real exports stay local and are never committed (`.gitignore` excludes `*.xlsx`, `*.xlsm`, `*.zip`).

## Data folder

`%LOCALAPPDATA%\RateGuardLite` (Windows), `~/Library/Application Support/RateGuardLite` (macOS), `~/.local/share/rateguard-lite` (Linux).

| File | Contents |
|---|---|
| `learning.db` | PROPOSED mapping items, run history, finding dispositions. (Older databases may still hold CONFIRMED rows; they are read, but the team file wins.) |
| `snapshots.db` | Only if Snapshot History is enabled: per-run normalized export rows (compressed) used as a baseline. |

Both are local SQLite files. **Confirmed mappings are not here**: they live in the repo file `config/team_mappings.yaml` (see below).

## Learning

Allow-listed kinds only: `TABLE_ALIAS`, `COLUMN_SYNONYM`, `COLUMN_ROLE`, `WILDCARD_TOKEN`, `LAYOUT_FINGERPRINT`, `RANGE_CONVENTION`, `JIRA_PATTERN`, `FINDING_DISPOSITION`.

- **`config/team_mappings.yaml` is the primary source of CONFIRMED items.** `rgl review`, `rgl map` and `rgl learn confirm` append to it with `confirmed_by` and `confirmed_on`. It is a plain file: review and commit it like any config change. Set `RGL_TEAM_MAPPINGS` to use a different location.
- The local SQLite store keeps only PROPOSED items (skipped review items, imported files) and run history.
- `rgl learn list|show|confirm|retire|diff` (team ids look like `T-1a2b3c`; `retire` on one removes it from the file).
- `rgl learn export --out file.yaml` and `rgl learn import file.yaml` (imported items are `PROPOSED` and need `rgl learn confirm`, unless `--trust` writes them straight to the team file). Use `export` once to move any legacy local CONFIRMED rows into the team file.
- `rgl stats` shows auto-mapped rate, suggestion acceptance, false-positive rate and manual mappings per run.

A `FALSE_POSITIVE` disposition only adds a hint next to an identical future finding. The finding still counts.

## Verdict without a baseline

`verdict.require_baseline_for_pass` (default `false`): a run with no baseline and no other issue is `PASS`; every report shows "Scope check (tables outside approved/Jira): not performed – no baseline provided". Set it to `true` (or use `--profile strict_baseline`) to return `PASS_WITH_LIMITATIONS` (exit 5) instead.

## Snapshot history

**Off by default** (`snapshots.enabled: false`; see D-024). Opt in with `--profile snapshots`, `snapshots.enabled: true`, or `--accept-snapshot` on a run. There is no end-of-run prompt. `rgl snapshot list|show|take|accept|purge`. Retention is `snapshots.retention_runs` (20) per rate book. A snapshot is used as a baseline only when `VERIFIED` and older (edition or effective date) than the current filing.

## Security posture

- A socket guard blocks every non-loopback connection at process start.
- Inputs are opened read-only; outputs go only to `--out`.
- No `eval`/`exec`/`pickle`/`os.system`; `subprocess` appears only in `rgl ui`. A test enforces this.
- Macros are never loaded, formulas never evaluated, external links never followed.
- UI: `127.0.0.1`, headless, telemetry off; uploads processed in a per-session temp folder that is deleted at exit and swept at startup.

## Troubleshooting

| Symptom | Action |
|---|---|
| Exit 3, "unsafe path" / "compression ratio" | The archive is suspicious; get a clean copy. |
| Exit 3, "encrypted" | Remove the password in Excel and re-save as `.xlsx`. |
| "No data-type row found" | Calibrate `gw_layouts.yaml` (see above). |
| Many `NEEDS_MAPPING` on day one | Run `rgl map --run <out>` once per table; later runs are automatic. |
| Slow on huge books | Check `run.log` stage timings; typical 220-table export: ~10 s. |
