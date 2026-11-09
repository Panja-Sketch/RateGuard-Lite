"""Print the headline numbers of a LAB_REPORT.json (and, with a second file, the change)."""
import json
import sys


def nums(p):
    m = json.load(open(p, encoding="utf-8"))
    s = m["summary"]
    return {"cases matched spec": f"{s['cases_matched']}/{s['cases']}", "planted caught clean": s["caught_clean"], "caught but noisy": s["caught_noisy"],
            "missed": s["missed"], "false alarms": s["false_alarms"], "gaps (tool)": s.get("tool_gaps"), "findings total": sum(len(r["findings"]) for c in m["cases"] for r in c["runs"])}, m

a, ma = nums(sys.argv[1])
b, mb = nums(sys.argv[2]) if len(sys.argv) > 2 else (None, None)
for k, v in a.items():
    print(f"{k:24} {v!s:>8}" + (f"  ->  {b[k]!s:>8}" if b else ""))
if mb:
    pa = {p["id"]: p["status"] for c in ma["cases"] for p in c["probes"]}
    pb = {p["id"]: p["status"] for c in mb["cases"] for p in c["probes"]}
    for k in pa:
        if pa[k] != pb.get(k):
            print("  probe", k, pa[k], "->", pb.get(k))
