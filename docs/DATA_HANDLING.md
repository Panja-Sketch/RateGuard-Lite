# Data handling statement

## What is read
The approved workbook(s), the Jira story file, the Guidewire export, and an optional baseline export. Files are opened **read-only**. Macros are never loaded or run; formulas are never evaluated (cached values are read); external links, OLE objects and data connections are never followed.

## Where it goes
Only to the `--out` folder you choose (reports, test scenarios, run log, manifest) and to two local SQLite files in your data folder (see the Admin guide).

## What is stored locally
| Store | Contents |
|---|---|
| `config/team_mappings.yaml` (in the repo, shared via git) | Confirmed table-name aliases, column names, tokens (e.g. `-1` is a wildcard), with the confirming user name and date. **No input file content.** |
| `learning.db` | Proposed (unconfirmed) mapping items using the same kinds of names, run ids, input **hashes**, verdicts, your OS username. **No input file content.** |
| `snapshots.db` | **Only if you opt in** (Snapshot History is off by default): the normalized rows of the exported rate book (compressed), used as a baseline. It stays on this machine; `rgl snapshot purge` deletes it. |
| `out/run.log` | Stages, counts, table IDs and rule IDs. Factor values only at DEBUG level (off by default). No absolute paths unless `--debug`. |

## What is never sent
Nothing is sent anywhere. There is no cloud component, telemetry or analytics. A socket guard raises an error on any outbound (non-loopback) connection. The optional UI listens on `127.0.0.1` only and has Streamlit usage statistics disabled.

## Reports
`reconciliation_report.*` and `test_scenarios.*` contain cell values and file/sheet/cell references from your inputs. Treat them with the same sensitivity as the inputs. The HTML report is self-contained (no external fonts, scripts or links) and escapes all values.

## Deleting data
- Reports: delete the `--out` folder.
- Snapshots: `rgl snapshot purge --all --yes`.
- Confirmed mappings: `rgl learn retire T-xxxxxx` removes the entry from `config/team_mappings.yaml` (git keeps the history). Proposals and run history: delete `learning.db`.
