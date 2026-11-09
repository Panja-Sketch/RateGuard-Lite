"""Scenario Lab runner.   python scenario_lab/run_lab.py [--only F6] [--skip-build] [--keep-going]

Builds every case's synthetic inputs, runs `rgl run` as a separate process for each run of each case,
compares the outcome with the EXPECTED values in cases.yaml (never edited after the fact), and writes
scenario_lab/out/LAB_REPORT.{html,xlsx,json}. Nothing under src/ is touched.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from build_cases import build_case, load_cases  # noqa: E402
from lab_report import write_reports  # noqa: E402

OUT = HERE / "out"


def rgl_command() -> list[str]:
    scripts = Path(sys.executable).parent
    for name in ("rgl.exe", "rgl"):
        if (scripts / name).exists():
            return [str(scripts / name)]
    return [sys.executable, "-c", "import sys; from rateguard_lite.cli import app; sys.exit(app())"]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def level_of(f: dict[str, Any]) -> str:
    return str(f.get("severity") or f.get("status") or "INFO")


def table_matches(f: dict[str, Any], num: int) -> bool:
    pat = re.compile(rf"(?<!\d){num}(?!\d)")
    return bool(pat.search(str(f.get("table_name", ""))) or pat.search(str(f.get("table_id", ""))))


def run_one(case: dict[str, Any], run: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    out = OUT / case["id"] / slug(run["name"])
    if out.exists():
        shutil.rmtree(out)
    data = OUT / "_data" / case["id"]
    data.mkdir(parents=True, exist_ok=True)
    cmd = [*rgl_command(), "run", "--approved", str(info["approved"]), "--jira", str(info["jira"]), "--export", str(info["export"]), "--out", str(out)]
    if run.get("baseline"):
        cmd += ["--baseline", str(info["baseline"])]
    env = {**os.environ, "RGL_DATA_DIR": str(data), "RGL_TEAM_MAPPINGS": str(data / "team_mappings.yaml"), "PYTHONIOENCODING": "utf-8"}
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=1800)
    runtime = time.perf_counter() - t0
    res: dict[str, Any] = {"name": run["name"], "baseline": bool(run.get("baseline")), "exit": proc.returncode, "runtime_s": round(runtime, 1),
                           "stderr": (proc.stderr or "")[-1500:], "stdout_tail": (proc.stdout or "")[-1500:], "findings": [], "must_tests": [],
                           "report_html": f"{case['id']}/{slug(run['name'])}/reconciliation_report.html"}
    rj = out / "reconciliation_report.json"
    if rj.exists():
        d = json.loads(rj.read_text(encoding="utf-8"))
        res["verdict"] = d["verdict"]
        res["gate"] = d.get("gate", "")
        res["summary"] = d.get("summary", {})
        res["jira"] = d.get("jira", {})
        res["problems"] = d.get("problems")            # D-030 grouped items (None on a build that has none)
        res["findings"] = [{**{k: f.get(k, "") for k in ("rule_id", "table_name", "table_id", "key", "column", "approved_value", "export_value", "message",
                                                         "plain_what", "plain_why", "plain_action")}, "level": level_of(f)} for f in d.get("findings", [])]
        ts = d.get("test_scenarios", {}) or {}
        res["must_tests"] = [{k: m.get(k, "") for k in ("priority", "what", "setup", "expect", "table", "category")} for m in ts.get("must_tests", [])]
        res["all_candidates_count"] = ts.get("all_candidates_count", 0)
    else:
        res["verdict"] = "BLOCKED" if proc.returncode in (2, 3) else "ERROR"
        res["gate"] = ((proc.stderr or proc.stdout or "").strip().splitlines() or [""])[0]
        res["error_text"] = (proc.stderr or proc.stdout or "").strip()[:1500]
    vt = out / "verdict.txt"
    res["verdict_text"] = vt.read_text(encoding="utf-8") if vt.exists() else ""
    return res


# ------------------------------------------------------------------ evaluation
def evaluate_run(run: dict[str, Any], res: dict[str, Any]) -> list[dict[str, Any]]:
    exp = run.get("expected", {})
    checks: list[dict[str, Any]] = []

    def chk(what: str, ok: bool, detail: str) -> None:
        checks.append({"what": what, "ok": bool(ok), "detail": detail})

    fs = res["findings"]
    rules = [f["rule_id"] for f in fs]
    chk("verdict", res["verdict"] == exp["verdict"], f"expected {exp['verdict']}, got {res['verdict']}")
    chk("exit code", res["exit"] == exp["exit"], f"expected {exp['exit']}, got {res['exit']}")
    for r in exp.get("rules", []):
        chk(f"finding {r} raised", r in rules, f"{rules.count(r)} found")
    for r in exp.get("forbid", []):
        chk(f"finding {r} NOT raised", r not in rules, f"{rules.count(r)} found")
    for r, n in (exp.get("counts") or {}).items():
        chk(f"exactly {n} x {r}", rules.count(r) == n, f"{rules.count(r)} found")
    if "max_total_findings" in exp:
        n = sum(1 for f in fs if f["level"] != "INFO")
        chk(f"at most {exp['max_total_findings']} findings above info level", n <= exp["max_total_findings"], f"{n} found")
    if "max_runtime_s" in exp:
        chk(f"finished within {exp['max_runtime_s']} s", res["runtime_s"] <= exp["max_runtime_s"], f"{res['runtime_s']} s")
    for text in exp.get("verdict_text_contains", []):
        chk(f"verdict text mentions '{text}'", text.lower() in res.get("verdict_text", "").lower(), "")
    jx = exp.get("jira")
    if jx:
        j = res.get("jira", {})
        if "lob" in jx:
            chk("Jira line of business read", j.get("lob") == jx["lob"], f"expected {jx['lob']}, got {j.get('lob')}")
        if "states" in jx:
            chk("Jira state read", list(j.get("states", [])) == list(jx["states"]), f"expected {jx['states']}, got {j.get('states')}")
        if jx.get("review_flags_nonempty"):
            chk("Jira ambiguity flagged for review", bool(j.get("review_flags")), f"flags: {j.get('review_flags')}")
    return checks


def evaluate_probe(p: dict[str, Any], res: dict[str, Any]) -> dict[str, Any]:
    num = p.get("table")
    fs = [f for f in res["findings"] if table_matches(f, num)] if num is not None else list(res["findings"])
    grouped = [g for g in (res.get("problems") or []) if num is not None and table_matches(g, num)]
    flagged = [f for f in fs if f["level"] != "INFO"]
    got = {f["rule_id"] for f in fs}
    any_ok = (not p.get("rules_any")) or bool(got & set(p["rules_any"]))
    all_ok = set(p.get("rules_all", [])) <= got
    bad = sorted(got & set(p.get("forbid", [])))
    status, note = "", ""
    if p["flag"]:
        if not flagged or not any_ok or not all_ok:
            status, note = "missed", ("Nothing was reported for this table." if not flagged else "The tool reported other kinds of problems on this table, but not the planted one.")
        elif bad:
            status, note = "partly", "Caught, but the tool also reported something that was not actually wrong."
        elif p.get("max_findings") and res.get("problems") is not None and num is not None and len(grouped) > p["max_findings"]:
            status, note = "partly", f"Caught, but shown as {len(grouped)} separate items (one root cause)."
        elif p.get("max_findings") and res.get("problems") is None and len(flagged) > p["max_findings"]:
            status, note = "partly", f"Caught, but as {len(flagged)} separate findings (one root cause)."
        else:
            status = "caught"
    else:
        status, note = ("false_alarm", f"The tool reported {len(flagged)} problem(s) on a table that was fine.") if (flagged or bad) else ("ok", "")
    return {"id": p["id"], "case": "", "run": res["name"], "items": p.get("items", []), "table": num, "what": p.get("what", ""), "flag": p["flag"], "status": status,
            "note": note, "limitation": p.get("limitation", "") if status == "missed" else "", "n_flagged": len(flagged), "rules": sorted(got),
            "sample": [{"rule_id": f["rule_id"], "level": f["level"], "plain_what": f["plain_what"]} for f in flagged[:4]]}


def evaluate_targets(case_id: str, res: dict[str, Any], targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for t in targets:
        if t["case"] != case_id or t.get("run", res["name"]) != res["name"]:
            continue
        g = [x for x in (res.get("problems") or []) if table_matches(x, t["table"])]
        kind = g[0].get("cause_kind", "") if g else ""
        out.append({"id": f"{case_id}-{t['table']}", "case": case_id, "table": t["table"], "expected": t["kind"], "actual": kind or "(no cause shown)",
                    "items": len(g), "ok": len(g) == 1 and kind == t["kind"], "what": t.get("what", ""),
                    "likely_cause": g[0].get("likely_cause", "") if g else ""})
    return out


def case_outcome(case: dict[str, Any], runs: list[dict[str, Any]], probes: list[dict[str, Any]]) -> str:
    matched = all(r["matched"] for r in runs)
    if any(p["status"] == "false_alarm" for p in probes):
        return "False alarm"
    if case["group"] == "happy":
        return "Caught" if (matched or all(r["verdict"] == "PASS" for r in runs)) else "False alarm"
    defect = [p for p in probes if p["flag"]]
    if not defect:
        return "Caught" if matched else "Missed"
    caught = [p for p in defect if p["status"] in ("caught", "partly")]
    if not caught:
        return "Missed"
    if all(p["status"] == "caught" for p in defect) and matched:
        return "Caught"
    return "Partly caught"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append")
    ap.add_argument("--skip-build", action="store_true", help="reuse inputs already in scenario_lab/out")
    a = ap.parse_args()
    spec = load_cases()
    targets = yaml.safe_load((HERE / "batch_targets.yaml").read_text(encoding="utf-8"))["diagnosis"]
    OUT.mkdir(exist_ok=True)
    results: list[dict[str, Any]] = []
    t_all = time.perf_counter()
    for case in spec["cases"]:
        if a.only and case["id"] not in a.only:
            continue
        print(f"== {case['id']}  {case['title']}", flush=True)
        if a.skip_build and (OUT / case["id"] / "inputs").exists():
            d = OUT / case["id"] / "inputs"
            man = json.loads((d / "inputs_manifest.json").read_text(encoding="utf-8"))
            info = {"approved": d / man["files"]["approved"], "export": d / "export.xlsx", "baseline": d / "baseline.xlsx", "jira": d / "jira.txt", "manifest": man}
        else:
            info = build_case(case, OUT)
        runs, probes = [], []
        for run in case["runs"]:
            res = run_one(case, run, info)
            res["checks"] = evaluate_run(run, res)
            res["matched"] = all(c["ok"] for c in res["checks"])
            res["diagnoses"] = evaluate_targets(case["id"], res, targets)
            res["expected"] = run["expected"]
            res["limitation"] = run.get("limitation", "")
            for p in case.get("probes", []):
                if p.get("run") in (None, run["name"]):
                    pr = evaluate_probe(p, res)
                    pr["case"] = case["id"]
                    probes.append(pr)
            runs.append(res)
            print(f"   [{run['name']}] verdict={res['verdict']} exit={res['exit']} findings={len(res['findings'])} {res['runtime_s']}s "
                  f"-> {'matches expectation' if res['matched'] else 'DIFFERS: ' + '; '.join(c['what'] + ' (' + c['detail'] + ')' for c in res['checks'] if not c['ok'])[:300]}", flush=True)
        results.append({"id": case["id"], "group": case["group"], "title": case["title"], "description": " ".join(case["description"].split()),
                        "catalog_items": case["catalog_items"], "tables": case["tables"], "runs": runs, "probes": probes,
                        "outcome": case_outcome(case, runs, probes), "manifest": info["manifest"]})
    total = round(time.perf_counter() - t_all, 1)
    write_reports(results, spec, OUT, total)
    print(f"\nLab finished in {total}s. Report: {OUT / 'LAB_REPORT.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
