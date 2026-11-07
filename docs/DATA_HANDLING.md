# Data handling statement

## What is read
The approved workbook(s), the Jira story file, the Guidewire export, and an optional baseline export. Files are opened **read-only**. Macros are never loaded or run; formulas are never evaluated (cached values are read); external links, OLE objects and data connections are never followed.

## Where it goes
Only to the `--out` folder you choose (reports, test scenarios, run log, manifest) and to two local SQLite files in your data folder (see the Admin guide).

## What is stored locally
| Store | Contents |
|---|---|
| `learning.db` | Table names, column names, header text, specific tokens you confirmed (e.g. `-1` is a wildcard), run ids, input **hashes**, verdicts, your OS username. **No input file content.** |
| `snapshots.db` | For each run, the normalized rows of the exported rate book (compressed). This is what lets the next run find undeclared changes. It stays on this machine; `rgl snapshot purge` deletes it. |
| `out/run.log` | Stages, counts, table IDs and rule IDs. Factor values only at DEBUG level (off by default). No absolute paths unless `--debug`. |

## What is never sent
Nothing is sent anywhere. There is no cloud component, telemetry or analytics. A socket guard raises an error on any outbound (non-loopback) connection. The optional UI listens on `127.0.0.1` only and has Streamlit usage statistics disabled.

## Reports
`reconciliation_report.*` and `test_scenarios.*` contain cell values and file/sheet/cell references from your inputs. Treat them with the same sensitivity as the inputs. The HTML report is self-contained (no external fonts, scripts or links) and escapes all values.

## Deleting data
- Reports: delete the `--out` folder.
- Snapshots: `rgl snapshot purge --all --yes`.
- Learned items: `rgl learn retire <id>` (history is retained by design); to remove everything, delete `learning.db`.
