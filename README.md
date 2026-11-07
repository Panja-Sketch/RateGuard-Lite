# RateGuard·Lite

**Did every approved rate land correctly in Guidewire?** RateGuard-Lite answers that, cell by cell, on your own machine.

It compares a Guidewire PolicyCenter rate book export with the **approved rate workbook** and the **Jira story**, and tells you:

- every approved factor that is wrong, missing, rounded, truncated or off by a decimal place;
- tables that changed but were never declared, and tables marked *DO NOT LOAD* that changed anyway;
- band boundaries that moved by one, gaps, overlaps, blanks loaded instead of wildcards;
- whether the edition and effective date match the filing;
- a targeted **QA test script** for exactly the rows that changed.

Everything runs **locally and offline**. Input documents never leave your machine (see [docs/DATA_HANDLING.md](docs/DATA_HANDLING.md)).

## Quick start

```powershell
# Windows (no admin rights needed; Python 3.11+)
git clone <your internal repo> ; cd RateGuard-Lite
.\setup.ps1                  # creates .venv, installs, runs the self-test
.\.venv\Scripts\Activate.ps1

rgl sample --out demo        # synthetic inputs (no real carrier data)
rgl run --approved demo\factor_mismatch\approved.xlsx `
        --jira     demo\factor_mismatch\jira.txt `
        --export   demo\factor_mismatch\export.xlsx `
        --baseline demo\factor_mismatch\baseline.xlsx `
        --out      demo\result
```

```bash
# macOS / Linux
./setup.sh && source .venv/bin/activate
```

Open `result/reconciliation_report.html` for the readable report. Prefer a screen? `rgl ui` starts the local UI on `127.0.0.1`.

## What you give it

| Input | Required | Notes |
|---|---|---|
| `--approved` | yes | Approved workbook (`.xlsx`/`.xlsm`), a folder of `.xlsx`, or a `.zip`. **Whole tables.** |
| `--jira` | yes | The story as `.txt`, `.md` or `.html` (copy-paste or *Export → HTML*). |
| `--export` | yes | Guidewire **Export to Spreadsheet** of the implemented rate book. |
| `--baseline` | no | Export of the same rate book *before* the change. If omitted, the tool uses its own **snapshot history**. |

## The verdict

| Verdict | Meaning | Exit code |
|---|---|---|
| `PASS` | Ready for UAT | 0 |
| `PASS_WITH_WARNINGS` / `PASS_WITH_LIMITATIONS` | Values verified; read the warnings / scope checks not performed | 5 |
| `NEEDS_REVIEW` | A human must confirm something (e.g. an unconfirmed table match) | 2 |
| `BLOCKED` | The tool could not verify (unmapped table, duplicate keys, unreadable cell) | 2 |
| `FAIL` | Do not promote; fix the listed items | 1 |
| unsafe/invalid input | Rejected before any parsing | 3 |

The tool **fails closed**: anything ambiguous is reported as needing mapping or review. It never prints `PASS` with open questions.

## Reports (in `--out`)

`verdict.txt` · `reconciliation_report.html` / `.xlsx` / `.json` · `test_scenarios.xlsx` / `.csv` · `jira_comment.txt` (paste into Jira) · `proposals.json` · `run_manifest.json` · `run.log`

## Commands

`run` · `drift` (export vs export, e.g. SAASQA vs GWUpdate) · `inspect` · `map` · `review` · `learn` · `stats` · `snapshot` · `ui` · `selftest` · `sample`. Run `rgl --help`.

## Learning, safely

When you confirm a table match once, the tool remembers it. Learning changes **how files are read and mapped** only. It can never change a comparison, a tolerance, a severity, or turn a FAIL into a PASS.

## Known limitations

- It verifies **rate table contents and scope**, not rating algorithms, premium maths, or Guidewire rate book selection logic (the transaction matrix gives QA tests for that).
- Guidewire export layouts vary. On a new Guidewire version run `rgl inspect FILE` first and adjust `config/gw_layouts.yaml` if needed.
- On the **first** run of a rate book with no `--baseline`, changes to tables outside the approved file cannot be detected (`PASS_WITH_LIMITATIONS`). From the second accepted run, Snapshot History provides the baseline. QA can run `rgl snapshot take --export <file>` *before* development starts to remove the gap.
- Jira parsing is pattern-based; unusual phrasing becomes `NEEDS_REVIEW`.
- The approved workbook is treated as truth. The tool does not validate actuarial correctness.
- Not yet implemented (see [docs/DECISIONS.md](docs/DECISIONS.md)): learned *layout* fingerprints applied to header detection, and the optional local-LLM Jira assist (config flag exists, default off).

## Developing

```bash
pip install -e ".[dev]"
pytest                  # unit + integration, all fixture scenarios
pytest -m slow          # performance acceptance (120 and 220 tables)
ruff check src tests && mypy --strict src/rateguard_lite --exclude 'ui/|samples/|cli.py'
```

Docs: [User guide](docs/USER_GUIDE.md) · [Admin guide](docs/ADMIN_GUIDE.md) · [Data handling](docs/DATA_HANDLING.md) · [Finding catalog](docs/FINDING_CATALOG.md) · [Decisions](docs/DECISIONS.md)
