"""Scenario Lab report writer: LAB_REPORT.html (self-contained, offline), LAB_REPORT.xlsx, LAB_REPORT.json."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

HERE = Path(__file__).resolve().parent
STATUS_LABEL = {"caught": "Caught", "ok": "Left alone (correct)", "partly": "Partly caught", "missed": "Missed", "false_alarm": "False alarm"}
GROUP_LABEL = {"happy": "Should pass", "failure": "Should be caught", "edge": "Edge case"}


# ------------------------------------------------------------------ model
def item_status(probes: list[dict[str, Any]]) -> str:
    if not probes:
        return "Not tested"
    if any(p["status"] == "false_alarm" for p in probes):
        return "False alarm"
    defect = [p for p in probes if p["flag"]]
    if not defect:
        return "Caught"
    got = [p for p in defect if p["status"] in ("caught", "partly")]
    if not got:
        return "Missed"
    return "Caught" if all(p["status"] == "caught" for p in defect) else "Partly caught"


def load_notes() -> dict[str, Any]:
    p = HERE / "gap_notes.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {} if p.exists() else {}


def build_gaps(results: list[dict[str, Any]], notes: dict[str, Any]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for c in results:
        for p in c["probes"]:
            if p["status"] not in ("missed", "partly", "false_alarm"):
                continue
            kind = {"missed": "Missed", "partly": "Noisy or partly caught", "false_alarm": "False alarm"}[p["status"]]
            n = notes.get(p["id"], {})
            gaps.append({"id": p["id"], "case": c["id"], "case_title": c["title"], "kind": kind, "items": p["items"],
                         "title": n.get("title") or p["what"], "plain": n.get("plain") or p["note"],
                         "observed": p["sample"], "cause": n.get("cause") or "Not yet investigated.", "fix": n.get("fix") or "To be decided by the product owner.",
                         "n_flagged": p["n_flagged"], "rules": p["rules"], "klass": n.get("klass", "tool_gap"), "severity": n.get("severity", "medium")})
        for r in c["runs"]:
            bad = [k for k in r["checks"] if not k["ok"]]
            if not bad:
                continue
            key = f"run:{c['id']}:{r['name']}"
            n = notes.get(key, {})
            if n.get("derived"):                       # consequence of a gap already listed under its own check
                continue
            gaps.append({"id": key, "case": c["id"], "case_title": c["title"], "kind": "Overall result differs", "items": c["catalog_items"],
                         "title": n.get("title") or f"{c['id']} ({r['name']}): overall result is not what the specification says",
                         "plain": n.get("plain") or "; ".join(f"{k['what']}: {k['detail']}" for k in bad),
                         "observed": [{"rule_id": f["rule_id"], "level": f["level"], "plain_what": f["plain_what"]} for f in r["findings"][:4]],
                         "cause": n.get("cause") or "Not yet investigated.", "fix": n.get("fix") or "To be decided by the product owner.", "n_flagged": len(r["findings"]), "rules": [],
                         "klass": n.get("klass", "tool_gap"), "severity": n.get("severity", "medium")})
    case_title = {c["id"]: c["title"] for c in results}
    for x in notes.get("extra", []):
        gaps.append({"id": x["id"], "case": x["case"], "case_title": case_title.get(x["case"], ""), "kind": x.get("kind", "Observation"), "items": x.get("items", []), "title": x["title"],
                     "plain": " ".join(x["plain"].split()), "observed": [], "cause": " ".join(x["cause"].split()), "fix": " ".join(x["fix"].split()), "n_flagged": 0, "rules": [],
                     "klass": x.get("klass", "tool_gap"), "severity": x.get("severity", "medium")})
    for g in gaps:
        for k in ("plain", "cause", "fix"):
            g[k] = " ".join(str(g[k]).split())
    order = {"high": 0, "medium": 1, "low": 2}
    gaps.sort(key=lambda g: (g["klass"] != "tool_gap", order.get(g["severity"], 1), g["case"]))
    return gaps


def build_model(results: list[dict[str, Any]], spec: dict[str, Any], total_s: float) -> dict[str, Any]:
    probes = [p for c in results for p in c["probes"]]
    defect = [p for p in probes if p["flag"]]
    control = [p for p in probes if not p["flag"]]
    runs = [r for c in results for r in c["runs"]]
    coverage = []
    for iid, name in spec["catalog"].items():
        ps = [p for p in probes if int(iid) in p["items"]]
        d = [p for p in ps if p["flag"]]
        coverage.append({"id": int(iid), "name": name, "status": item_status(ps), "probes": [p["id"] for p in ps], "cases": sorted({p["case"] for p in ps}),
                         "planted": len(d), "planted_caught": sum(1 for p in d if p["status"] in ("caught", "partly")), "clean_ok": sum(1 for p in ps if not p["flag"] and p["status"] == "ok"),
                         "clean_bad": sum(1 for p in ps if not p["flag"] and p["status"] == "false_alarm")})
    summary = {
        "cases": len(results), "runs": len(runs), "cases_matched": sum(1 for c in results if all(r["matched"] for r in c["runs"])),
        "cases_documented_limit": sum(1 for c in results if not all(r["matched"] for r in c["runs"]) and all(r["matched"] or r.get("limitation") for r in c["runs"])),
        "missed_documented": sum(1 for p in defect if p["status"] == "missed" and p.get("limitation")),
        "runs_matched": sum(1 for r in runs if r["matched"]),
        "planted": len(defect), "caught_clean": sum(1 for p in defect if p["status"] == "caught"),
        "caught_noisy": sum(1 for p in defect if p["status"] == "partly"), "missed": sum(1 for p in defect if p["status"] == "missed"),
        "controls": len(control), "false_alarms": sum(1 for p in control if p["status"] == "false_alarm"),
        "diag_targets": sum(len(r.get("diagnoses", [])) for r in runs), "diag_met": sum(1 for r in runs for d in r.get("diagnoses", []) if d["ok"]),
        "runtime_total_s": total_s, "runtime_tool_s": round(sum(r["runtime_s"] for r in runs), 1),
        "slowest": max(((r["runtime_s"], c["id"], r["name"]) for c in results for r in c["runs"]), default=(0, "", "")),
        "catalog_total": len(coverage), "catalog_by_status": {s: sum(1 for x in coverage if x["status"] == s) for s in ("Caught", "Partly caught", "Missed", "False alarm", "Not tested")},
    }
    gaps = build_gaps(results, load_notes())
    summary["gaps"] = len(gaps)
    summary["tool_gaps"] = sum(1 for g in gaps if g["klass"] == "tool_gap")
    summary["documented_limits"] = sum(1 for g in gaps if g["klass"] == "limitation")
    summary["expectation_off"] = sum(1 for g in gaps if g["klass"] == "expectation_off")
    summary["tool_gaps_high"] = sum(1 for g in gaps if g["klass"] == "tool_gap" and g["severity"] == "high")
    return {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "summary": summary, "coverage": coverage, "cases": results, "gaps": gaps, "resolved": load_notes().get("resolved", []), "legend": STATUS_LABEL}


# ------------------------------------------------------------------ xlsx
def write_xlsx(m: dict[str, Any], path: Path) -> None:
    wb = Workbook()
    head = PatternFill("solid", fgColor="12284C")
    fills = {"Caught": "D6F0E0", "Partly caught": "FFF0C2", "Missed": "F9D2D2", "False alarm": "E4D4F4", "Not tested": "E6E6E6"}

    def sheet(title: str, header: list[str], rows: list[list[Any]], widths: list[int], status_col: int | None = None) -> None:
        ws = wb.create_sheet(title)
        ws.append(header)
        for c in ws[1]:
            c.font, c.fill = Font(bold=True, color="FFFFFF"), head
        for r in rows:
            ws.append([str(x) if isinstance(x, (list, dict)) else x for x in r])
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
            if status_col is not None and row[status_col].value in fills:
                row[status_col].fill = PatternFill("solid", fgColor=fills[row[status_col].value])
        ws.freeze_panes = "A2"

    first = wb.active
    assert first is not None
    wb.remove(first)
    s = m["summary"]
    sheet("Summary", ["Measure", "Value"], [
        ["Cases run", s["cases"]], ["Runs executed (with / without before-export)", s["runs"]], ["Cases that matched the specification", s["cases_matched"]],
        ["Planted problems", s["planted"]], ["Caught cleanly", s["caught_clean"]], ["Caught but noisy", s["caught_noisy"]], ["Missed", s["missed"]],
        ["Clean tables checked", s["controls"]], ["False alarms", s["false_alarms"]], ["Gaps listed", s["gaps"]],
        ["  of which tool gaps", s["tool_gaps"]], ["  of which our expectation was off", s["expectation_off"]],
        ["Tool time (s)", s["runtime_tool_s"]], ["Whole lab time (s)", s["runtime_total_s"]]], [48, 16])
    sheet("Coverage", ["#", "Catalog item", "Status", "Cases", "Checks"], [[c["id"], c["name"], c["status"], ", ".join(c["cases"]), ", ".join(c["probes"])] for c in m["coverage"]], [6, 34, 16, 18, 40], 2)
    rows = []
    for c in m["cases"]:
        for r in c["runs"]:
            rows.append([c["id"], c["title"], GROUP_LABEL[c["group"]], r["name"], c["description"], r.get("gate", ""), r["expected"]["verdict"], r["verdict"],
                         r["exit"], r["runtime_s"], "yes" if r["matched"] else "NO", c["outcome"], len(r["findings"]), len(r["must_tests"]),
                         " | ".join(t["what"] for t in r["must_tests"][:3])])
    sheet("Cases", ["Case", "Title", "Type", "Run", "What we changed", "What the tool said", "Expected verdict", "Actual verdict", "Exit", "Seconds", "Matches spec", "Caught?",
                    "Findings", "Must tests", "First must tests"], rows, [7, 34, 14, 14, 50, 50, 14, 14, 6, 8, 10, 14, 9, 9, 60], 11)
    sheet("Checks", ["Check", "Case", "Run", "Catalog items", "What", "Planted?", "Result", "Note", "Findings on table", "Finding IDs"],
          [[p["id"], p["case"], p["run"], ", ".join(map(str, p["items"])), p["what"], "yes" if p["flag"] else "no (control)", STATUS_LABEL[p["status"]], p["note"], p["n_flagged"], ", ".join(p["rules"])]
           for c in m["cases"] for p in c["probes"]], [12, 7, 14, 12, 50, 14, 20, 50, 10, 40])
    sheet("Gaps", ["Is it the tool?", "Impact", "Kind", "Case", "What", "In plain words", "Likely cause", "Suggested fix (not implemented)"],
          [[{"tool_gap": "Tool gap", "limitation": "Documented limitation"}.get(g["klass"], "Our expectation was off"), g["severity"], g["kind"], g["case"], g["title"], g["plain"], g["cause"], g["fix"]] for g in m["gaps"]],
          [18, 9, 20, 7, 44, 60, 60, 60])
    fr = [[c["id"], r["name"], f["rule_id"], f["level"], f["table_name"], f["key"], f["column"], f["approved_value"], f["export_value"], f["plain_what"]]
          for c in m["cases"] for r in c["runs"] for f in r["findings"]][:20000]
    sheet("Findings", ["Case", "Run", "Finding ID", "Level", "Table", "Row", "Column", "Approved", "PolicyCenter", "In plain words"], fr, [7, 14, 26, 12, 30, 28, 20, 14, 14, 80])
    wb.save(path)


# ------------------------------------------------------------------ html
HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RateGuard-Lite Scenario Lab Report</title>
<style>
:root{--navy:#12284c;--navy2:#1c3a6b;--teal:#0e9aa7;--ink:#1d2433;--mut:#5b6577;--line:#dfe4ec;--bg:#f4f6fa;--card:#fff;
--ok:#d6f0e0;--okt:#146c3b;--part:#fff0c2;--partt:#8a5a00;--miss:#f9d2d2;--misst:#a11d1d;--fa:#e4d4f4;--fat:#5b2a8c;--nt:#e6e8ec;--ntt:#555}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 "Segoe UI",system-ui,-apple-system,Roboto,Arial,sans-serif;color:var(--ink);background:var(--bg)}
header{background:linear-gradient(135deg,var(--navy),var(--navy2));color:#fff;padding:22px 32px}
header .brand{display:flex;align-items:center;gap:12px;font-weight:700;font-size:20px;letter-spacing:.2px}
header .brand svg{flex:none}header small{display:block;opacity:.8;font-weight:400;margin-top:4px}
main{max-width:1280px;margin:0 auto;padding:24px 24px 60px}h2{font-size:19px;margin:34px 0 10px;color:var(--navy)}
h2 span{font-weight:400;color:var(--mut);font-size:14px;margin-left:8px}
.lead{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--teal);border-radius:8px;padding:16px 20px;font-size:16px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-top:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px}.card b{display:block;font-size:26px;line-height:1.1;color:var(--navy)}
.card span{color:var(--mut);font-size:13px}.card.bad b{color:var(--misst)}.card.good b{color:var(--okt)}.card.warn b{color:var(--partt)}.card.fa b{color:var(--fat)}
.legend{display:flex;flex-wrap:wrap;gap:8px 16px;margin:6px 0 12px;font-size:13px;color:var(--mut)}
.pill{display:inline-block;padding:2px 10px;border-radius:12px;font-size:12.5px;font-weight:600;white-space:nowrap}
.s-Caught,.s-caught,.s-ok{background:var(--ok);color:var(--okt)}.s-Partly,.s-partly{background:var(--part);color:var(--partt)}.s-Missed,.s-missed{background:var(--miss);color:var(--misst)}
.s-Falsealarm,.s-false_alarm{background:var(--fa);color:var(--fat)}.s-Nottested{background:var(--nt);color:var(--ntt)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(235px,1fr));gap:8px}
.chip{border-radius:8px;padding:8px 10px;font-size:13.5px;display:flex;gap:8px;align-items:flex-start;border:1px solid rgba(0,0,0,.06);cursor:default}
.chip i{font-style:normal;font-weight:700;min-width:26px}.chip small{display:block;opacity:.8;font-size:11.5px}
.bar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:8px 0 10px}
select,input[type=search]{padding:7px 10px;border:1px solid var(--line);border-radius:6px;font:inherit;background:#fff}input[type=search]{min-width:260px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden}
th{background:var(--navy);color:#fff;text-align:left;font-weight:600;padding:9px 10px;font-size:13px}td{padding:9px 10px;border-top:1px solid var(--line);vertical-align:top;font-size:14px}
tr.case{cursor:pointer}tr.case:hover td{background:#eef6f8}tr.detail td{background:#fafbfd;padding:14px 18px}
.id{font-weight:700;color:var(--navy);white-space:nowrap}.mut{color:var(--mut)}.small{font-size:13px}
ol.mt{margin:4px 0 0 18px;padding:0;font-size:13px}.mt li{margin:2px 0}
details{margin:8px 0}summary{cursor:pointer;color:var(--teal);font-weight:600}
table.in{font-size:12.5px;margin-top:6px}table.in th{background:#e9eef6;color:var(--navy)}table.in td{padding:5px 8px}
.gap{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px 18px;margin:10px 0}.gap h3{margin:0 0 4px;font-size:16px;color:var(--navy)}
.gap dl{margin:8px 0 0;display:grid;grid-template-columns:150px 1fr;gap:4px 12px;font-size:14px}.gap dt{color:var(--mut)}.gap dd{margin:0}
a{color:var(--teal)}.ok{color:var(--okt);font-weight:600}.no{color:var(--misst);font-weight:600}
footer{color:var(--mut);font-size:12.5px;margin-top:30px}
@media(max-width:700px){main{padding:16px}.gap dl{grid-template-columns:1fr}}
</style></head><body>
<header><div class="brand"><svg width="30" height="34" viewBox="0 0 30 34"><path d="M15 1 28 6v10c0 8-5.600 14.500-13 17C7.600 30.500 2 24 2 16V6z" fill="#0e9aa7"/><path d="M9 16l4 4 8-9" stroke="#fff" stroke-width="3" fill="none"/></svg>
<div>RateGuard<span style="color:#7fe0e8">·</span>Lite &mdash; Scenario Lab<small id="gen"></small></div></div></header>
<main>
<div class="lead" id="lead"></div><div class="cards" id="cards"></div>
<h2>Coverage by catalog item <span>one tile per item the tool is meant to check</span></h2>
<div class="legend" id="legend"></div><div class="grid" id="grid"></div>
<h2>Every case <span>click a row to see what was checked</span></h2>
<div class="bar"><label>Show <select id="f"><option value="">all cases</option><option>Caught</option><option>Partly caught</option><option>Missed</option><option>False alarm</option></select></label>
<input type="search" id="q" placeholder="Search cases, tables, findings..."><span class="mut small" id="cnt"></span></div>
<table id="tbl"><thead><tr><th>Case</th><th>What we changed</th><th>What the tool said</th><th>Expected vs actual</th><th>Caught?</th><th>Must tests suggested</th></tr></thead><tbody></tbody></table>
<h2>Gaps found <span id="gapn"></span></h2><div id="gaps"></div>
<h2>Fixed since the first run <span id="resn"></span></h2><div id="resolved"></div>
<footer>All data in this lab is synthetic. Expected results were written from the specification before the first run and were not changed afterwards. The tool itself was not modified.</footer>
</main>
<script id="data" type="application/json">/*DATA*/</script>
<script>
const M=JSON.parse(document.getElementById('data').textContent);
const $=s=>document.querySelector(s);
const esc=t=>String(t==null?'':t).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const CM={'Caught':'s-Caught','Partly caught':'s-Partly','Missed':'s-Missed','False alarm':'s-Falsealarm','Not tested':'s-Nottested',caught:'s-Caught',ok:'s-Caught',partly:'s-Partly',missed:'s-Missed',false_alarm:'s-Falsealarm'};
const cls=s=>CM[s]||'s-Nottested';
const S=M.summary;$('#gen').textContent='Generated '+M.generated+' · synthetic data only';
const missedTot=S.missed, noisy=S.caught_noisy;
$('#lead').innerHTML=`We ran <b>${S.cases} cases</b> (${S.runs} runs). <b>${S.cases_matched}</b> behaved exactly as the specification says${S.cases_documented_limit?' ('+S.cases_documented_limit+' more differ only by a documented limitation)':''}. We planted <b>${S.planted}</b> problems:
the tool caught <b>${S.caught_clean+S.caught_noisy}</b> (${S.caught_clean} cleanly, ${S.caught_noisy} with noisy or partly wrong output) and <b>missed ${S.missed}</b>. It was also shown <b>${S.controls}</b> tables that were fine and raised a <b>false alarm on ${S.false_alarms}</b>. We list <b>${S.gaps} gaps</b>: <b>${S.tool_gaps}</b> are real shortcomings of the tool (${S.tool_gaps_high} of high impact) and <b>${S.expectation_off}</b> are places where our own expectation was too strict or out of date. The tool needed ${S.runtime_tool_s} seconds in total; the slowest run was ${S.slowest[1]} (${S.slowest[0]} s).`;
const cards=[['Cases run',S.cases,''],['Matched the specification',S.cases_matched+' of '+S.cases,S.cases_matched==S.cases?'good':'warn'],['Problems planted',S.planted,''],
['Caught cleanly',S.caught_clean,'good'],['Caught but noisy',S.caught_noisy,'warn'],['Missed',S.missed,S.missed?'bad':'good'],['Clean tables checked',S.controls,''],['False alarms',S.false_alarms,S.false_alarms?'fa':'good'],['Real tool gaps',S.tool_gaps,S.tool_gaps?'bad':'good'],['Likely causes as intended',S.diag_met+' of '+S.diag_targets,S.diag_met==S.diag_targets?'good':'warn'],['Run time (tool)',S.runtime_tool_s+' s','']];
$('#cards').innerHTML=cards.map(c=>`<div class="card ${c[2]}"><b>${esc(c[1])}</b><span>${c[0]}</span></div>`).join('');
const LG=[['Caught','Handled as the specification expects (a planted problem was found, or a fine table was left alone)'],['Partly caught','Found, but noisy or only some of the planted problems'],['Missed','A planted problem was not reported'],['False alarm','Something fine was reported as a problem'],['Not tested','No check for this item']];
$('#legend').innerHTML=LG.map(l=>`<span title="${esc(l[1])}"><span class="pill ${cls(l[0])}">${l[0]}</span> ${esc(l[1])}</span>`).join('');
$('#grid').innerHTML=M.coverage.map(c=>`<div class="chip ${cls(c.status)}" title="Checks: ${esc(c.probes.join(', '))}"><i>#${c.id}</i><div>${esc(c.name)}<small>${c.status} · ${c.cases.join(', ')}${c.planted?' · '+c.planted_caught+' of '+c.planted+' planted caught':''}${c.clean_ok||c.clean_bad?' · '+c.clean_ok+' of '+(c.clean_ok+c.clean_bad)+' clean ok':''}</small></div></div>`).join('');
const verdictPill=(v,ok)=>`<span class="pill ${ok?'s-Caught':'s-Missed'}">${esc(v)}</span>`;
function rowHtml(c,i){
 const r0=c.runs[0];
 const said=c.runs.map(r=>`${c.runs.length>1?'<b>'+esc(r.name)+':</b> ':''}${esc(r.gate||'')}${r.findings.length?'<div class="mut small">'+esc(r.findings[0].plain_what)+'</div>':''}`).join('<hr style="border:0;border-top:1px solid #e5e8ee">');
 const ev=c.runs.map(r=>`${c.runs.length>1?'<span class="small mut">'+esc(r.name)+'</span><br>':''}expected ${esc(r.expected.verdict)}<br>got ${verdictPill(r.verdict,r.verdict==r.expected.verdict)}`).join('<br>');
 const mt=c.runs.map(r=>`${r.must_tests.length} test(s)<ol class="mt">${r.must_tests.slice(0,3).map(t=>'<li>'+esc(t.what)+'</li>').join('')}</ol>`).join('');
 return `<tr class="case" data-i="${i}"><td class="id">${c.id}<div class="mut small" style="font-weight:400">${esc({happy:'Should pass',failure:'Should be caught',edge:'Edge case'}[c.group])}</div></td>
 <td><b>${esc(c.title)}</b><div class="mut small">${esc(c.description)}</div></td><td>${said}</td><td>${ev}</td><td><span class="pill ${cls(c.outcome)}">${c.outcome}</span></td><td>${mt}</td></tr>`}
function detailHtml(c){
 return c.runs.map(r=>{
  const chk=r.checks.map(k=>`<tr><td>${esc(k.what)}</td><td class="${k.ok?'ok':'no'}">${k.ok?'yes':'NO'}</td><td>${esc(k.detail)}</td></tr>`).join('');
  const pr=c.probes.filter(p=>p.run==r.name).map(p=>`<tr><td>${esc(p.id)}</td><td>${esc(p.what)}</td><td>${p.flag?'planted':'fine'}</td><td><span class="pill ${cls(p.status)}">${({caught:'Caught',ok:'Left alone',partly:'Partly caught',missed:'Missed',false_alarm:'False alarm'})[p.status]}</span></td><td>${esc(p.note)}</td><td>${p.n_flagged}</td></tr>`).join('');
  const fr=r.findings.slice(0,80).map(f=>`<tr><td>${esc(f.rule_id)}</td><td>${esc(f.level)}</td><td>${esc(f.table_name)}</td><td>${esc(f.key)}</td><td>${esc(f.plain_what)}</td></tr>`).join('');
  const mt=r.must_tests.map(t=>`<li>${esc(t.what)}<div class="mut">${esc(t.setup)} &rarr; ${esc(t.expect)}</div></li>`).join('');
  return `<div><h4 style="margin:6px 0">${c.runs.length>1?esc(r.name)+' &mdash; ':''}${r.matched?'<span class="ok">matches the specification</span>':(r.limitation?'<span class="no">differs from the specification &mdash; documented limitation ('+esc(r.limitation)+')</span>':'<span class="no">differs from the specification</span>')}
  <span class="mut small">&nbsp;${r.runtime_s} s</span> &nbsp;<a href="${esc(r.report_html)}" target="_blank">open the tool's own report</a></h4>
  <details open><summary>What we checked (${pr?c.probes.filter(p=>p.run==r.name).length:0})</summary><table class="in"><tr><th>Check</th><th>What</th><th></th><th>Result</th><th>Note</th><th>Findings</th></tr>${pr}</table></details>
  ${(r.diagnoses||[]).length?`<details open><summary>Likely cause shown by the tool (${r.diagnoses.filter(d=>d.ok).length} of ${r.diagnoses.length} as intended)</summary><table class="in"><tr><th>Table</th><th>As intended?</th><th>Items shown</th><th>Likely cause</th></tr>${r.diagnoses.map(d=>`<tr><td>${esc(d.table)} ${esc(d.what)}</td><td class="${d.ok?'ok':'no'}">${d.ok?'yes':'NO'}</td><td>${d.items}</td><td>${esc(d.likely_cause)}</td></tr>`).join('')}</table></details>`:''}
  <details><summary>Must tests the tool suggested (${r.must_tests.length})</summary><ol class="mt">${mt}</ol></details>
  <details><summary>Technical details &mdash; overall result checks and findings (${r.findings.length})</summary><table class="in"><tr><th>Check</th><th>Met?</th><th>Detail</th></tr>${chk}</table>
  <table class="in"><tr><th>Finding ID</th><th>Level</th><th>Table</th><th>Row</th><th>In plain words</th></tr>${fr}</table>${r.findings.length>80?'<div class="mut small">first 80 of '+r.findings.length+' shown; all are in LAB_REPORT.xlsx</div>':''}
  ${r.error_text?'<pre class="small">'+esc(r.error_text)+'</pre>':''}</details></div>`}).join('<hr>')}
function render(){
 const f=$('#f').value,q=$('#q').value.toLowerCase();let n=0;const tb=$('#tbl tbody');tb.innerHTML='';
 M.cases.forEach((c,i)=>{
  const hay=(c.id+' '+c.title+' '+c.description+' '+JSON.stringify(c.runs.map(r=>[r.gate,r.findings.map(x=>x.plain_what+x.table_name+x.rule_id)]))+JSON.stringify(c.tables)).toLowerCase();
  if((f&&c.outcome!==f)||(q&&!hay.includes(q)))return;n++;
  tb.insertAdjacentHTML('beforeend',rowHtml(c,i));
 });$('#cnt').textContent=n+' of '+M.cases.length+' cases';}
$('#tbl').addEventListener('click',e=>{const tr=e.target.closest('tr.case');if(!tr||e.target.closest('a'))return;const nx=tr.nextElementSibling;
 if(nx&&nx.classList.contains('detail')){nx.remove();return}
 tr.insertAdjacentHTML('afterend',`<tr class="detail"><td colspan="6">${detailHtml(M.cases[+tr.dataset.i])}</td></tr>`)});
$('#f').onchange=render;$('#q').oninput=render;render();
$('#resn').textContent='('+M.resolved.length+')';
$('#resolved').innerHTML=M.resolved.map(r=>`<div class="gap" style="padding:8px 16px"><span class="pill s-Caught">Fixed in ${esc(r.fixed_in)}</span> ${esc(r.title)}</div>`).join('');
$('#gapn').textContent='('+M.gaps.length+')';
$('#gaps').innerHTML=M.gaps.length?M.gaps.map(g=>`<div class="gap"><h3>${esc(g.title)} <span class="pill ${g.kind=='Missed'?'s-Missed':g.kind=='False alarm'?'s-Falsealarm':'s-Partly'}">${esc(g.kind)}</span> <span class="pill ${g.klass=='tool_gap'?'s-Missed':'s-Nottested'}">${g.klass=='tool_gap'?'Tool gap · '+esc(g.severity)+' impact':g.klass=='limitation'?'Documented limitation':'Our expectation was off'}</span></h3>
 <div class="mut small">Case ${esc(g.case)} &middot; ${esc(g.case_title)}${g.items.length?' &middot; catalog item(s) '+g.items.map(i=>'#'+i).join(', '):''}</div>
 <dl><dt>In plain words</dt><dd>${esc(g.plain)}</dd><dt>Likely cause</dt><dd>${esc(g.cause)}</dd><dt>Suggested fix</dt><dd>${esc(g.fix)} <span class="mut small">(not implemented)</span></dd></dl>
 <details><summary>Technical details</summary><div class="small">${g.observed.length?g.observed.map(o=>'<div><code>'+esc(o.rule_id)+'</code> ['+esc(o.level)+'] '+esc(o.plain_what)+'</div>').join(''):'No findings on this table.'}<div class="mut">Finding IDs on this table: ${esc(g.rules.join(', ')||'none')}</div></div></details></div>`).join(''):'<div class="gap">No gaps: every planted problem was caught cleanly and no fine table was flagged.</div>';
</script></body></html>"""


def write_html(m: dict[str, Any], path: Path) -> None:
    data = json.dumps(m, ensure_ascii=False).replace("</", "<\\/")
    path.write_text(HTML.replace("/*DATA*/", data), encoding="utf-8")


def write_reports(results: list[dict[str, Any]], spec: dict[str, Any], out: Path, total_s: float) -> None:
    m = build_model(results, spec, total_s)
    (out / "LAB_REPORT.json").write_text(json.dumps(m, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_xlsx(m, out / "LAB_REPORT.xlsx")
    write_html(m, out / "LAB_REPORT.html")
