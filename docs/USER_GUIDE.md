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

## 4. Common situations

**"NEEDS_MAPPING / BLOCKED: table could not be matched."**
The approved tab name and the Guidewire table name look too different. Run `rgl map --run results\PC-12345`, pick the right Guidewire table once, then re-run. The tool remembers it for next time.

**"NEEDS_REVIEW: matched by similarity."** Confirm it with `rgl review results\PC-12345`.

**"PASS_WITH_LIMITATIONS: no baseline."**
All approved values were verified, but changes to *other* tables could not be checked. Add `--baseline`, or answer *yes* to "Accept this export as the accepted state?" at the end of a passing run so the next run has a baseline. Better still: before the developer starts, run `rgl snapshot take --export <current QA export>`.

**"Struck-through row."** A row is crossed out in the approved file. Confirm with actuarial whether it should be deleted.

**"Rounded match."** Guidewire stores fewer decimals than the approved value. This is fine unless the difference is material (then you get a `PRECISION_TRUNCATION` warning).

**Comparing two environments?** `rgl drift --left SAASQA.xlsx --right GWUpdate.xlsx --out drift\` lists tables that differ cell by cell.

## 5. First time on a new Guidewire version

Run `rgl inspect Export.xlsx`. It shows, for each sheet, where the tool found the header and the data types and which columns it treats as keys and factors. If it cannot find the layout, it says so; send the output (not the file) to your admin so they can adjust `config/gw_layouts.yaml`.

## 6. Exit codes (for scripts)

0 PASS · 1 FAIL · 2 BLOCKED or NEEDS_REVIEW · 3 unsafe or invalid input · 4 internal error · 5 passed with warnings or limitations.
