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
| `learning.db` | Learned items (append-only versions), run history, finding dispositions. |
| `snapshots.db` | Per-run normalized export rows (compressed) used as the baseline. |

Both are local SQLite files. Back them up like any team asset, or share mappings via `rgl learn export/import`.

## Learning

Allow-listed kinds only: `TABLE_ALIAS`, `COLUMN_SYNONYM`, `COLUMN_ROLE`, `WILDCARD_TOKEN`, `LAYOUT_FINGERPRINT`, `RANGE_CONVENTION`, `JIRA_PATTERN`, `FINDING_DISPOSITION`.

- `rgl learn list|show|retire|diff`
- `rgl learn export --out team_mappings.yaml` and `rgl learn import team_mappings.yaml` (imported items are `PROPOSED` and need local confirmation, unless `--trust`).
- Keep `team_mappings.yaml` in an internal Git repo to share across the team.
- `rgl stats` shows auto-mapped rate, suggestion acceptance, false-positive rate and manual mappings per run.

A `FALSE_POSITIVE` disposition only adds a hint next to an identical future finding. The finding still counts.

## Snapshot history

`rgl snapshot list|show|take|accept|purge`. Retention is `snapshots.retention_runs` (20) per rate book. A snapshot is used as a baseline only when `VERIFIED` and older (edition or effective date) than the current filing.

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
