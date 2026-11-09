# First run on real files (runbook)

For the person who will run RateGuard-Lite on real organisation files for the first time. Install the tool first ([INSTALL.md](INSTALL.md)). Allow about half a day for the first ticket, mostly for calibration.

The tool runs **offline**, only reads the files you give it, and checks **rate tables**. It does not check rating logic or premiums.

---

## 1. Data rules (read these first)

| Rule | Why |
|---|---|
| Keep real files in **`C:\RGL-Data\<ticket>\`** (for example `C:\RGL-Data\PC-12345\`), **outside** the repo folder. | The repo is shared through git; real files must never end up in it. |
| **Never commit** real files, reports or screenshots of them. `.gitignore` already excludes `*.xlsx`, `*.xlsm`, `*.xls`, `*.zip`, `out/`, `*.db`, but it is not a safety net for everything (for example `.txt` or `.html` exports of a Jira story). Check `git status` before every commit. | Real carrier data. |
| Write results to the same ticket folder: `--out C:\RGL-Data\<ticket>\result`. | Keeps one ticket in one place. |
| **Delete** ticket folders when your organisation's retention policy says so. The tool does not delete anything for you. | Data-handling policy. |
| The tool makes **no network calls** (a guard blocks sockets). The only local servers are the optional UI on `127.0.0.1`. | See [DATA_HANDLING.md](DATA_HANDLING.md). |

Create the folder:

```powershell
New-Item -ItemType Directory -Force C:\RGL-Data\PC-12345 | Out-Null
```

---

## 2. Prepare the three inputs

| Input | Where it comes from | Format |
|---|---|---|
| **Approved workbook** | The Jira ticket attachment or the actuary. One workbook, a folder of workbooks, or a `.zip`. | `.xlsx` / `.xlsm` (one tab per table; the tab name or its first cell carries the table number/name) |
| **Guidewire export** | PolicyCenter **QA** environment: *Product Designer / Rate Books* → open the rate book → **Export to Spreadsheet**. Export **after** the change was loaded. | `.xlsx` |
| **Jira story** | Either **.txt** (copy *Summary* + *Description* + the key comments into a text file) or **.html** (Jira's own export; it keeps strikethrough). | `.txt`, `.md` or `.html` |

**Strikethrough rule.** If the story uses strikethrough to mean "do not load" (or "removed from scope"), a plain `.txt` copy **loses the strikethrough**. Write the words **DO NOT LOAD** next to that table in the `.txt`. For example:

```
Table 705 Wind Relativity - DO NOT LOAD (struck through in the story)
```

Optional but recommended: a **"before" export** of the same rate book (before the change), passed with `--baseline`. Without it the tool cannot see changes to tables outside the story (see section 6).

Save everything in the ticket folder:

```
C:\RGL-Data\PC-12345\approved.xlsx
C:\RGL-Data\PC-12345\export.xlsx
C:\RGL-Data\PC-12345\jira.txt
C:\RGL-Data\PC-12345\baseline.xlsx     (optional)
```

---

## 3. Calibrate on the first real files

Guidewire layouts differ slightly between versions and teams. Before trusting any result, look at how the tool **reads** your files.

```powershell
.\.venv\Scripts\Activate.ps1
rgl inspect C:\RGL-Data\PC-12345\export.xlsx --max-sheets 5
rgl inspect C:\RGL-Data\PC-12345\approved.xlsx --max-sheets 5
```

`inspect` prints, for the first sheets, what it detected. **Do not paste real values anywhere**; read it on your own screen.

### Checklist: what correct output looks like

For the **export**, for each sheet shown:

- [ ] **Header row**: the row that holds the column names (for example *Territory*, *Class Code*, *Factor*) is the one reported, not a title or blank row.
- [ ] **Data-type row**: the row with `DECIMAL`, `INTEGER`, `STRING` ... is found (the tool uses it to locate the header).
- [ ] **Key columns** are the columns that identify a row (territory, class, deductible, band limits ...).
- [ ] **Factor column(s)** are the numbers to compare (factor, rate, relativity, amount ...), with a sensible decimal scale (for example 4).
- [ ] **Rate book code** (for example `BOPByPeril_OR_RateBook`) is read.
- [ ] **Edition** is read.
- [ ] **Effective date** is read and is the filing date, not today's date.
- [ ] **State / jurisdiction** is read and matches the ticket.

For the **approved workbook**: each tab is recognised as a table, its key and factor columns look right, and the table number or name is what you expect.

### If something is wrong

Edit [`config/gw_layouts.yaml`](../config/gw_layouts.yaml) (the first profile is used), then run `rgl inspect` again. Typical changes:

| Symptom | Change |
|---|---|
| "No data-type row ... found" | The type words differ. Add them to `type_tokens`, for example `NUMERIC`, `VARCHAR`. |
| The header is read from the wrong row | The header sits **above** the type row: set `header_position: above_type`. |
| Edition / effective date / state is empty | Your export labels them differently. Add the label to `metadata_labels`, for example `effective_date: ["effective date", "rate book effective date", "eff date"]`. |
| A numeric column is treated as a key | Add its word to `factor_markers`, for example `loss cost`. |

Example: the export labels the date as "Eff. Dt" and the types as `NUMERIC(10,4)`:

```yaml
    metadata_labels:
      effective_date: ["effective date", "effectivedate", "rate book effective date", "eff. dt"]
    type_tokens: [DECIMAL, INTEGER, STRING, DATE, BOOLEAN, DOUBLE, LONG, NUMBER, TEXT, BIGDECIMAL, INT, BOOL, DATETIME, NUMERIC]
```

Labels are compared in lower case. Keep the quotes and the square brackets. Changes to `config/` are normal git changes: review them with `git diff config` before committing.

---

## 4. The trust ladder (pass the steps in order)

Do not use the tool as a gate until all three steps are done.

| Step | What you do | You may continue when |
|---|---|---|
| **1. Known-good old ticket** | Pick a ticket that was fully checked by hand and was correct. Run the tool on its files. | Verdict is **PASS** (or PASS WITH WARNINGS with warnings you can explain). If it finds problems, investigate: either a calibration issue (section 3) or a real mistake nobody noticed. |
| **2. Known-defect old ticket** | Pick a ticket where QA found a real defect. Run the tool on the files as they were when the defect existed. | The tool **reports that defect**. |
| **3. Live tickets in parallel** | Run the tool on **3 to 5** live tickets next to your normal QA. Compare. | The tool found everything QA found, and the false alarms are few and understood. |

Keep a log. Copy this template into a spreadsheet kept **outside the repo** (never put real values in it, only counts and ticket keys):

| Ticket | LOB | State | Tables | Verdict | True issues found | False alarms | Missed by tool (found by QA) | Minutes saved | Notes |
|---|---|---|---|---|---|---|---|---|---|
| PC-_____ | BOP | __ | __ | PASS / FAIL / ... | 0 | 0 | 0 | __ | |

Run command for every ticket (without a baseline; add `--baseline` when you have one):

```powershell
rgl run --approved C:\RGL-Data\PC-12345\approved.xlsx `
        --jira     C:\RGL-Data\PC-12345\jira.txt `
        --export   C:\RGL-Data\PC-12345\export.xlsx `
        --baseline C:\RGL-Data\PC-12345\baseline.xlsx `
        --out      C:\RGL-Data\PC-12345\result
```

Open `C:\RGL-Data\PC-12345\result\reconciliation_report.html` in your browser. Start with the **verdict**, then the **Problems by table** tab. Exit codes: `0` PASS, `1` FAIL, `2` needs review or blocked, `3` an input file could not be used, `5` passed with limitations or warnings.

---

## 5. Mapping: "needs mapping"

On the first runs the tool may say a table or column **needs mapping** (it could not tell which export table belongs to which approved tab, or what a column means). Resolve it once and the answer is remembered:

```powershell
rgl map --run C:\RGL-Data\PC-12345\result
```

Answer each question (confirm or reject). Confirmed answers are written to **`config/team_mappings.yaml`**, a plain text file shared by the team through git.

- **Commit only names, never values.** The file should contain table names, column names and roles, and nothing that looks like a rate.
- **Review with `git diff config/team_mappings.yaml`** before you commit. Remove anything you do not understand.
- Re-run the ticket afterwards; the "needs mapping" items should be gone.

---

## 6. Known limits (plain words)

- **No "before" export, no scope check.** Without `--baseline` the tool cannot see whether a table **outside** the change was altered. It says so in the report ("We did not check tables outside this change"). Supply `--baseline` whenever you can.
- **Tables marked DO NOT LOAD** cannot be checked for changes without a before-export either. The report tells you once to check them by hand.
- **It checks tables, not rating logic.** It does not calculate premiums, and it does not know whether a table is wired into the right rating step.
- **A new layout needs calibration** (section 3). Until the checklist is satisfied, a PASS means little.
- **Only what is in the export.** If a table was not exported, it is not checked.
- **The date check for tables outside the change is switched off by default** (decision D-032).
- The tool helps QA; it does not replace it.

---

## 7. What to send back when you need help

Send:

1. The **`rgl inspect` output with values masked** (replace real numbers and codes with `xxx`; keep the structure: sheet names, header words, types, roles).
2. **`verdict.txt`** from the result folder (read it first: it contains table names and counts, and sometimes values; mask values).
3. The **exact error message** (the red box, or `run.log` from the result folder; it contains no input data).
4. Your tool version (`rgl --version`) and Python version.

**Never send the files** (approved workbook, export, baseline, Jira text) or screenshots that show real values.
