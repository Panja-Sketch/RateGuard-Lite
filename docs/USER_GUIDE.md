# User guide (for QA analysts and actuarial reviewers)

You do not need to be a developer to use RateGuard-Lite.

## 1. What you need

1. **The Jira story**: copy the description and comments into a `.txt` file, or use Jira's *Export → HTML*.
2. **The approved workbook** from actuarial/product (the whole table on each tab).
3. **The Guidewire export** of the rate book after the developer loaded the change (*Export to Spreadsheet*).

Optional: an export of the same rate book **before** the change (`--baseline`).

## 2. Run it

```
rgl run --approved Approved.xlsx --jira PC-12345.txt --export RateBook_QA.xlsx --out results\PC-12345
```

Or start the screen version: `rgl ui`, then upload the files in your browser. Nothing is sent anywhere.

## 3. Read the result

Open `results\PC-12345\reconciliation_report.html`.

- The coloured banner at the top is the verdict (see the table in the README).
- **Next steps** tells you what to fix first.
- The **Findings** tab lists every problem. Click a row to see *what it means*, *how to fix it* and the exact cells in both files.
- **Scope audit** shows each table: in Jira? in the approved file? in the export? changed?
- **QA scenarios** is your test script: boundary tests for each changed band plus the transaction and date matrix.

To tell the team, paste `jira_comment.txt` into the story.

### Problems by table
The report groups everything found in one table into one item: how many cells, what kind of problem, and a likely cause when the evidence is clear (for example "values look copied from table 551" or "the same rows are written differently: '300k' and '300000'"). Open an item to see its rows. The full cell-by-cell list is still in the All findings tab and in the Excel sheet Discrepancies.

## 4. Common situations

**"NEEDS_MAPPING / BLOCKED: table could not be matched."**
The approved tab name and the Guidewire table name look too different. Run `rgl map --run results\PC-12345`, pick the right Guidewire table once, then re-run. The choice is written to `config/team_mappings.yaml` with your name and the date. Commit that file so teammates get the mapping too.

**"NEEDS_REVIEW: matched by similarity."** Confirm it with `rgl review results\PC-12345`.

**"Scope check ... not performed – no baseline provided."**
You ran without `--baseline`. All approved values were verified, but changes to *other* tables could not be compared, so that check was not done (the verdict can still be PASS). To run it, add `--baseline <export of the rate book before the change>`. If your team's profile sets `verdict.require_baseline_for_pass: true`, such a run is `PASS_WITH_LIMITATIONS` instead.

**"NEEDS_REVIEW: OUT_OF_SCOPE_TABLE_DATED."** (only if your team has switched this optional check on; it is off by default)
A table that is not in the approved workbook or the Jira story carries this filing's effective date or edition in its own metadata, so someone may have loaded or edited it by mistake. Ask the developer, then either add it to the story and approved file or restore its old date/edition. If the export has no per-table dates (or every table has the same stamp) this check is skipped and noted in `run_manifest.json`.

**"I skipped a suggestion in `rgl review`."** It is kept locally as PROPOSED and is not used. See `rgl learn list --status PROPOSED`, then `rgl learn confirm <id>` to write it to the team file.

**"Struck-through row."** A row is crossed out in the approved file. Confirm with actuarial whether it should be deleted.

**"Rounded match."** Guidewire stores fewer decimals than the approved value. This is fine unless the difference is material (then you get a `PRECISION_TRUNCATION` warning).

**Comparing two environments?** `rgl drift --left SAASQA.xlsx --right GWUpdate.xlsx --out drift\` lists tables that differ cell by cell.

## 5. First time on a new Guidewire version

Run `rgl inspect Export.xlsx`. It shows, for each sheet, where the tool found the header and the data types and which columns it treats as keys and factors. If it cannot find the layout, it says so; send the output (not the file) to your admin so they can adjust `config/gw_layouts.yaml`.

## 6. Exit codes (for scripts)

0 PASS · 1 FAIL · 2 BLOCKED or NEEDS_REVIEW · 3 unsafe or invalid input · 4 internal error · 5 passed with warnings or limitations.

## 7. How to read the messages (plain language)

Every item in the report has three parts: **what we found**, **why it matters**, **what to do**. Technical detail (rule name, raw values, cell locations) is folded under "Technical details" in the HTML report, and has its own column in the Excel report.

**Before and after**

| | Before | After |
|---|---|---|
| A wrong factor | `[CRITICAL] 121 BLD Number of Stories — VALUE_MISMATCH: Stories From=4: approved 1.045 vs export 1.025 (-1.9%)` | Table '121 BLD Number of Stories' (Stories From = 4): the Stories Factor value in PolicyCenter is not the approved value. Approved 1.045, PolicyCenter has 1.025 (1.9% lower). **Why it matters:** policies with Stories From = 4 will be undercharged. **What to do:** change the value in PolicyCenter to 1.045 and export the rate book again. |
| No "before" export | `Scope check (tables outside approved/Jira): not performed – no baseline provided` | We did not check tables outside this change, because no 'before' export was provided. |
| A password-protected file | `Workbook is encrypted/password-protected (or an old .xls format). Where: export.xlsx` | We couldn't open export.xlsx because it's password-protected (encrypted), or it's an old .xls file. Next: remove the password in Excel, save a plain .xlsx copy, and run again. |
| Headline | `Do not promote: fix listed items` | Some values in PolicyCenter don't match what was approved. Fix these before QA. |

**The Must Test list.** `test_scenarios.xlsx` opens on **Must Test**: a short list (12 by default) of quotes worth running by hand, most important first. It does not repeat the value checks the tool already did. After a fix, the first rows retest the exact row that was wrong. The **All Candidates** sheet shows every idea the tool scored and why each one was kept or left out. Use `--tests full` for the earlier long lists as well.
