"""Drift report writers: environment export vs export."""

from __future__ import annotations

import json
from pathlib import Path

import openpyxl

from ..engines.drift import DriftResult
from .writer import _sheet


def write_drift(res: DriftResult, out: Path, left_name: str, right_name: str) -> None:
    summary = {
        "left": left_name, "right": right_name, "left_edition": res.left_meta.get("edition"), "right_edition": res.right_meta.get("edition"),
        "identical": len(res.identical), "differing": len(res.differing), "only_left": len(res.only_left), "only_right": len(res.only_right),
        "needs_mapping": len(res.needs_mapping), "differences": len(res.diffs),
    }
    (out / "drift_report.json").write_text(json.dumps({"summary": summary, "differing": res.differing, "only_left": res.only_left,
                                                       "only_right": res.only_right, "needs_mapping": res.needs_mapping, "diffs": res.diffs}, indent=2, sort_keys=True), encoding="utf-8")
    wb = openpyxl.Workbook()
    first = wb.active
    assert first is not None
    wb.remove(first)
    _sheet(wb, "Summary", ["Item", "Value"], [[k, v] for k, v in summary.items()], [26, 50])
    _sheet(wb, "Tables", ["Table", "Status"],
           [[t, "identical"] for t in res.identical] + [[t, "differing"] for t in res.differing] + [[t, "only in left"] for t in res.only_left] +
           [[t, "only in right"] for t in res.only_right] + [[t, "needs mapping"] for t in res.needs_mapping], [50, 20])
    _sheet(wb, "Differences", ["Table", "Type", "Key", "Column", f"Left ({left_name})", f"Right ({right_name})", "Left cell", "Right cell"],
           [[d["table"], d["type"], d["key"], d["column"], d["left"], d["right"], d["left_ref"], d["right_ref"]] for d in res.diffs], [30, 16, 36, 22, 18, 18, 36, 36])
    wb.save(out / "drift_report.xlsx")
    lines = [f"DRIFT: {left_name} vs {right_name}", f"identical={len(res.identical)} differing={len(res.differing)} only_left={len(res.only_left)} only_right={len(res.only_right)} needs_mapping={len(res.needs_mapping)}"]
    lines += [f"  {d['table']} | {d['type']} | {d['key']} | {d['column']} | {d['left']} -> {d['right']}" for d in res.diffs[:50]]
    (out / "drift_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
