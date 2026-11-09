"""Build the demonstration page docs/demo/index.html (with screenshots) from the synthetic 'factor_mismatch' sample.

Usage (from the repo folder, environment activated):
    python scripts/make_demo.py

What it does, with made-up data only:
  1. writes the synthetic sample inputs (rgl sample) into a temp folder,
  2. runs RateGuard-Lite on them (approved workbook + Jira story + Guidewire export + before-export),
  3. renders the Jira story, the approved sheet, the export sheet (the wrong cell highlighted), the terminal
     verdict and the HTML report to PNG files with headless Chrome/Edge (needs one of them installed),
  4. writes docs/demo/index.html that tells the story in order, and copies the generated report next to it.
Nothing leaves this machine.
"""
from __future__ import annotations

import html
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import openpyxl
from PIL import Image, ImageChops

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "docs" / "demo"
IMG = OUT / "img"
SCENARIO = "factor_mismatch"
TABLE_NO = "121"


def find_browser() -> str:
    cands = [os.environ.get("RGL_BROWSER", ""),
             r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
             r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
             "/usr/bin/google-chrome", "/usr/bin/chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    for c in cands:
        if c and Path(c).exists():
            return c
    for name in ("chrome", "chromium", "msedge", "google-chrome"):
        w = shutil.which(name)
        if w:
            return w
    raise SystemExit("No Chrome/Edge found. Install one or set RGL_BROWSER to its path.")


def rgl(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-c", "from rateguard_lite.cli import app; app()", *args], capture_output=True, text=True, encoding="utf-8", env=env)


def shot(browser: str, page: Path, png: Path, width: int, height: int, tmp: Path) -> None:
    """Screenshot a local HTML file, then trim the empty space below the content."""
    raw = tmp / ("raw_" + png.name)
    prof = tmp / "browser_profile"
    subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={prof}", f"--screenshot={raw}",
                    f"--window-size={width},{height}", "--force-device-scale-factor=1", page.resolve().as_uri()], check=True, capture_output=True, timeout=120)
    im = Image.open(raw).convert("RGB")
    bg = Image.new("RGB", im.size, im.getpixel((im.width - 1, im.height - 1)))
    box = ImageChops.difference(im, bg).getbbox()
    if box:
        im = im.crop((0, 0, im.width, min(im.height, box[3] + 24)))
    im.save(png, optimize=True)


CSS = """
body{margin:0;padding:24px;background:#f4f6f8;font-family:'Segoe UI',Arial,sans-serif;color:#1f2933}
.card{background:#fff;border:1px solid #d5dbe1;border-radius:8px;padding:18px 22px;max-width:880px}
h2{margin:0 0 10px;font-size:18px} .cap{color:#52606d;font-size:13px;margin:0 0 12px}
pre{margin:0;white-space:pre-wrap;font:13px/1.5 Consolas,'Courier New',monospace}
table.x{border-collapse:collapse;font:13px Calibri,'Segoe UI',Arial,sans-serif;background:#fff}
table.x th,table.x td{border:1px solid #c8ced4;padding:3px 10px;min-width:92px;height:20px}
table.x th{background:#eef1f4;color:#52606d;font-weight:600;text-align:center}
table.x td.n{text-align:right} table.x td.rn{background:#eef1f4;color:#52606d;text-align:center;min-width:24px}
td.bad{background:#fde2e1;color:#a31515;font-weight:700;outline:2px solid #d64545} td.good{background:#e3f4e6}
.meta td:first-child{font-weight:600;background:#fafbfc} .typ td{font-style:italic;color:#52606d;background:#fafbfc}
.tabs{display:flex;gap:2px;margin-top:10px;font:12px 'Segoe UI',Arial,sans-serif} .tabs span{padding:4px 12px;background:#e4e7eb;border:1px solid #c8ced4;border-top:none;border-radius:0 0 5px 5px;color:#52606d}
.tabs span.on{background:#fff;color:#107c41;font-weight:600;border-bottom:3px solid #107c41}
.term{background:#0f1720;color:#e6edf3;border-radius:8px;padding:16px 20px;max-width:880px} .term pre{font-size:12.5px;line-height:1.35}
.note{margin-top:12px;padding:8px 12px;background:#fff8e1;border-left:4px solid #f0b429;font-size:13px}
"""


def page(title: str, body: str, path: Path) -> None:
    path.write_text(f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title><style>{CSS}</style></head><body>{body}</body></html>", encoding="utf-8")


def col_letter(i: int) -> str:
    return "ABCDEFGHIJ"[i]


def sheet_html(ws, title: str, caption: str, tabs: list[str], active: str, bad: set[tuple[int, int]], good: set[tuple[int, int]], note: str) -> str:
    rows = list(ws.iter_rows(values_only=True))
    ncol = max(len(r) for r in rows)
    out = [f"<div class='card'><h2>{html.escape(title)}</h2><p class='cap'>{html.escape(caption)}</p><table class='x'><tr><th></th>"
           + "".join(f"<th>{col_letter(i)}</th>" for i in range(ncol)) + "</tr>"]
    for ri, r in enumerate(rows):
        cls = "meta" if ri < 7 and (len(r) > 1 and r[1] is not None and not isinstance(r[1], (int, float))) and r[0] in ("Rate Book Code", "Edition", "Table Code", "Table Name", "Effective Date", "Jurisdiction", "Line of Business") else ("typ" if r and r[0] in ("INTEGER", "STRING", "DECIMAL") else "")
        out.append(f"<tr class='{cls}'><td class='rn'>{ri + 1}</td>")
        for ci in range(ncol):
            v = r[ci] if ci < len(r) else None
            c = "bad" if (ri, ci) in bad else ("good" if (ri, ci) in good else ("n" if isinstance(v, (int, float)) else ""))
            if (ri, ci) in bad or (ri, ci) in good:
                c += " n"
            out.append(f"<td class='{c.strip()}'>{'' if v is None else html.escape(str(v))}</td>")
        out.append("</tr>")
    out.append("</table><div class='tabs'>" + "".join(f"<span class='{'on' if t == active else ''}'>{html.escape(t)}</span>" for t in tabs[:6]) + "<span>...</span></div>")
    if note:
        out.append(f"<div class='note'>{note}</div>")
    out.append("</div>")
    return "".join(out)


def main() -> int:
    browser = find_browser()
    tmp = Path(tempfile.mkdtemp(prefix="rgl_demo_"))
    try:
        env = dict(os.environ, RGL_DATA_DIR=str(tmp / "data"), RGL_TEAM_MAPPINGS=str(tmp / "team_mappings.yaml"), PYTHONIOENCODING="utf-8", NO_COLOR="1", COLUMNS="100")
        (tmp / "data").mkdir()
        r = rgl(["sample", "--out", str(tmp / "sample")], env)
        if r.returncode:
            print(r.stdout, r.stderr)
            return 1
        sd = tmp / "sample" / SCENARIO
        res = tmp / "result"
        run = rgl(["run", "--approved", str(sd / "approved.xlsx"), "--jira", str(sd / "jira.txt"), "--export", str(sd / "export.xlsx"), "--baseline", str(sd / "baseline.xlsx"), "--out", str(res)], env)
        if run.returncode != 1:
            print("expected exit code 1 (FAIL), got", run.returncode, run.stdout, run.stderr)
            return 1
        term = run.stdout.split("Reports written to")[0].rstrip()

        IMG.mkdir(parents=True, exist_ok=True)
        # 1 Jira story
        jira = (sd / "jira.txt").read_text(encoding="utf-8")
        page("jira", f"<div class='card'><h2>1. The Jira story</h2><p class='cap'>What the business asked for (made-up ticket).</p><pre>{html.escape(jira)}</pre></div>", tmp / "p1.html")
        shot(browser, tmp / "p1.html", IMG / "01_jira_story.png", 960, 900, tmp)

        # 2 approved workbook, 3 export
        wa = openpyxl.load_workbook(sd / "approved.xlsx")
        we = openpyxl.load_workbook(sd / "export.xlsx")
        an = next(n for n in wa.sheetnames if n.startswith(TABLE_NO))
        en = "BOP_BLD_NumStories_Ext"
        a_rows = {(r0[0], r0[1]): r0[2] for r0 in wa[an].iter_rows(min_row=2, values_only=True)}
        bad: set[tuple[int, int]] = set()
        good: set[tuple[int, int]] = set()
        wrong = None
        for i, row in enumerate(we[en].iter_rows(values_only=True)):
            key = (row[0], row[1])
            if key in a_rows and isinstance(row[2], (int, float)) and i >= 10:
                if abs(row[2] - a_rows[key]) > 1e-12:
                    bad.add((i, 2))
                    wrong = (key, a_rows[key], row[2])
        for i, row in enumerate(wa[an].iter_rows(values_only=True)):
            if wrong and i > 0 and (row[0], row[1]) == wrong[0]:
                good.add((i, 2))
        (key, appr, got) = wrong
        page("approved", sheet_html(wa[an], "2. The approved workbook (from the actuary)", f"Tab '{an}'. The approved factor for {key[0]} to {key[1]} stories is {appr}.", wa.sheetnames, an, set(), good, ""), tmp / "p2.html")
        shot(browser, tmp / "p2.html", IMG / "02_approved_workbook.png", 960, 700, tmp)
        page("export", sheet_html(we[en], "3. The Guidewire export (what was loaded in PolicyCenter)", f"Sheet '{en}' of the export. Someone loaded {got} instead of {appr}.", we.sheetnames, en, bad, set(),
                                  f"Highlighted: Stories {key[0]} to {key[1]} has <b>{got}</b>, the approved value is <b>{appr}</b>. An eyeball review of ~90 cells can easily miss this."), tmp / "p3.html")
        shot(browser, tmp / "p3.html", IMG / "03_guidewire_export.png", 960, 760, tmp)

        # 4 terminal
        page("terminal", f"<div class='term'><pre>$ rgl run --approved approved.xlsx --jira jira.txt --export export.xlsx --baseline baseline.xlsx --out result\n\n{html.escape(term)}\n\n(exit code 1 = FAIL)</pre></div>", tmp / "p4.html")
        shot(browser, tmp / "p4.html", IMG / "04_tool_verdict.png", 960, 1100, tmp)

        # 5/6 HTML report
        rep = res / "reconciliation_report.html"
        shot(browser, rep, IMG / "05_report_top.png", 1280, 820, tmp)
        shot(browser, rep, IMG / "06_report_full.png", 1280, 3000, tmp)

        # 7 verdict.txt (what goes to Jira)
        vt = (res / "verdict.txt").read_text(encoding="utf-8")
        page("verdict", f"<div class='card'><h2>7. verdict.txt: the short text to paste into Jira, and the Must Test list for QA</h2><pre>{html.escape(vt)}</pre></div>", tmp / "p7.html")
        shot(browser, tmp / "p7.html", IMG / "07_verdict_and_must_test.png", 1000, 1900, tmp)

        # copy the generated reports
        rep_dir = OUT / "sample_report"
        rep_dir.mkdir(parents=True, exist_ok=True)
        for name in ("reconciliation_report.html", "verdict.txt", "jira_comment.txt"):
            shutil.copy2(res / name, rep_dir / name)
        for f in rep_dir.glob("*"):
            if str(Path.home()) in f.read_text(encoding="utf-8", errors="ignore"):
                f.write_text(f.read_text(encoding="utf-8").replace(str(Path.home()), "~"), encoding="utf-8")

        write_index(appr, got, key, term)
        print("Demo written to", OUT / "index.html")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def write_index(appr: float, got: float, key: tuple[int, int], term: str) -> None:
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RateGuard-Lite demo</title>
<style>
:root{{--bg:#f4f6f8;--fg:#1f2933;--mut:#52606d;--card:#fff;--line:#d5dbe1;--acc:#0e9aa7;--bad:#c0392b;--ok:#1e8449}}
@media (prefers-color-scheme:dark){{:root{{--bg:#11181f;--fg:#e6edf3;--mut:#9fb0c0;--card:#18222c;--line:#2c3a47}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 'Segoe UI',system-ui,Arial,sans-serif}}
main{{max-width:1000px;margin:0 auto;padding:28px 16px 60px}}
h1{{margin:0 0 4px;font-size:28px}} h2{{margin:36px 0 6px;font-size:20px}} p{{margin:6px 0}} .lead{{color:var(--mut);font-size:17px}}
figure{{margin:14px 0;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px;overflow:hidden}}
figure img{{display:block;max-width:100%;height:auto;border-radius:6px}} figcaption{{color:var(--mut);font-size:14px;padding:8px 4px 2px}}
.steps{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin:18px 0}}
.steps div{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px;font-size:14px}} .steps b{{display:block;color:var(--acc)}}
.verdict{{display:inline-block;padding:2px 10px;border-radius:6px;background:var(--bad);color:#fff;font-weight:700}}
a{{color:var(--acc)}} code{{background:var(--card);border:1px solid var(--line);border-radius:4px;padding:0 5px}}
.tag{{font-size:13px;color:var(--mut)}}
</style></head><body><main>
<h1>RateGuard&middot;Lite: a 60-second demonstration</h1>
<p class="lead">Did every approved rate land correctly in Guidewire? Here, one factor was loaded wrong. The tool finds it in seconds and tells QA exactly what to fix and what to test.</p>
<p class="tag">All data below is synthetic (generated by <code>rgl sample</code>); nothing is real carrier data. Regenerate with <code>python scripts/make_demo.py</code>.</p>

<div class="steps"><div><b>1. Jira story</b>says what should change</div><div><b>2. Approved workbook</b>the correct numbers</div><div><b>3. Guidewire export</b>what is actually loaded</div>
<div><b>4. RateGuard-Lite</b>compares every cell, offline</div><div><b>5. Verdict + report</b>what to fix, what to test</div></div>

<h2>1. The input: a Jira story</h2>
<p>An Oregon BOP rate revision: six tables to update, one new table, one table marked <i>DO NOT LOAD</i>.</p>
<figure><img src="img/01_jira_story.png" alt="Jira story text"><figcaption>The story names the tables to change and the one to leave alone.</figcaption></figure>

<h2>2. The approved workbook</h2>
<p>The actuary's workbook has one tab per table. For <b>121 BLD Number of Stories</b>, buildings with {key[0]} to {key[1]} stories get a factor of <b>{appr}</b>.</p>
<figure><img src="img/02_approved_workbook.png" alt="Approved workbook sheet"><figcaption>Approved: the correct value is highlighted in green.</figcaption></figure>

<h2>3. The Guidewire export: the mistake</h2>
<p>After the change was loaded in PolicyCenter, the rate book was exported. In that table the {key[0]} to {key[1]} stories row holds <b>{got}</b> instead of {appr}, a 1.9% undercharge on every policy with a 4 to 6 story building. It is one cell among 89 (and on a real rate book, among tens of thousands).</p>
<figure><img src="img/03_guidewire_export.png" alt="Guidewire export sheet with the wrong cell highlighted"><figcaption>Exported: the wrong value is highlighted in red.</figcaption></figure>

<h2>4. Run the tool</h2>
<p>One command, a few seconds, on the laptop, no internet. The verdict is <span class="verdict">FAIL</span> and it names the table, the row, the approved value and the loaded value.</p>
<figure><img src="img/04_tool_verdict.png" alt="Terminal output of rgl run"><figcaption>The command-line result. Exit code 1 means FAIL, so it can also gate a script.</figcaption></figure>

<h2>5. The report</h2>
<p>The HTML report opens on <b>Problems by table</b>: one item per table, with a count and a likely cause. The other tabs list every finding, every table, and the QA test script.</p>
<figure><img src="img/05_report_top.png" alt="HTML report: verdict and problems by table"><figcaption>Top of <code>reconciliation_report.html</code>. <a href="sample_report/reconciliation_report.html">Open the real generated report</a>.</figcaption></figure>
<figure><img src="img/06_report_full.png" alt="HTML report, full page"><figcaption>The whole report page.</figcaption></figure>

<h2>6. What goes back to Jira and to QA</h2>
<p><code>verdict.txt</code> (and <code>jira_comment.txt</code>) is a short plain-words summary to paste into the ticket: what needs fixing, why it matters, what to do, and a short ranked <b>Must Test</b> list so QA tests exactly the rows that changed instead of everything.</p>
<figure><img src="img/07_verdict_and_must_test.png" alt="verdict.txt with next steps and must-test list"><figcaption>Plain-language findings, next steps and the Must Test list. Files: <a href="sample_report/verdict.txt">verdict.txt</a>, <a href="sample_report/jira_comment.txt">jira_comment.txt</a>.</figcaption></figure>

<h2>What this shows</h2>
<ul>
<li>It checks <b>every approved cell</b> against Guidewire, not a sample.</li>
<li>The answer is plain: table, row, approved value, loaded value, difference, what to do.</li>
<li>It runs <b>offline</b>; files never leave the laptop.</li>
<li>It also catches missing or extra rows, shifted bands, blanks loaded as wildcards, undeclared changes, wrong dates and editions (see <a href="../FINDING_CATALOG.md">FINDING_CATALOG.md</a>).</li>
<li>It checks tables, not rating logic or premiums, and it helps QA rather than replacing it.</li>
</ul>
<p>Try it yourself: <a href="../INSTALL.md">INSTALL.md</a>, then <code>rgl sample --out demo</code>. On real files: <a href="../FIRST_REAL_RUN.md">FIRST_REAL_RUN.md</a>.</p>
</main></body></html>"""
    (OUT / "index.html").write_text(body, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
