"""Every fixture scenario (spec 17) produces its expected verdict and finding IDs (A1, A3, A8)."""

import json
import re

import pytest

from rateguard_lite.samples.generator import SCENARIOS
from rateguard_lite.samples.runner import check, run_scenario

FAST = [n for n in SCENARIOS if n != "export_220_tables_perf"]


@pytest.mark.parametrize("name", FAST)
def test_scenario(name, scenario, tmp_path):
    sc = scenario(name)
    o = run_scenario(sc, tmp_path)
    assert check(sc, o) == [], o.error


def test_clean_scenarios_have_no_critical_or_high(scenario, tmp_path):        # A3
    for name in ("clean_pass", "rounded_match_only"):
        o = run_scenario(scenario(name), tmp_path)
        bad = [f for f in o.result.findings if f.level in ("CRITICAL", "HIGH", "BLOCKER")]
        assert bad == []


def test_unsafe_inputs_exit_3_with_clear_message(scenario, tmp_path):        # A8
    for name in ("unsafe_zip_traversal", "encrypted_file"):
        o = run_scenario(scenario(name), tmp_path)
        assert o.exit_code == 3 and "Next:" in o.error and "Where:" in o.error


def test_reports_written_and_self_contained(scenario, tmp_path):
    sc = scenario("factor_mismatch")
    o = run_scenario(sc, tmp_path, write_reports=True)
    out = tmp_path / sc.name
    for f in ("verdict.txt", "reconciliation_report.json", "reconciliation_report.xlsx", "reconciliation_report.html", "test_scenarios.xlsx",
              "test_scenarios.csv", "jira_comment.txt", "proposals.json", "run_manifest.json", "run.log"):
        assert (out / f).exists(), f
    html = (out / "reconciliation_report.html").read_text(encoding="utf-8")
    assert not re.search(r"(src|href)=[\"']https?://", html) and "<link" not in html
    assert "FAIL" in (out / "verdict.txt").read_text(encoding="utf-8")
    log = (out / "run.log").read_text(encoding="utf-8")
    assert "1.0450" not in log and str(tmp_path) not in log                    # no values / absolute paths in the log
    data = json.loads((out / "reconciliation_report.json").read_text(encoding="utf-8"))
    assert data["verdict"] == "FAIL" and data["findings"][0]["rule_id"] == "VALUE_MISMATCH"
    assert o.result.findings[0].approved_ref and o.result.findings[0].export_ref          # evidence refs
    for f in o.result.findings:
        assert f.explanation and f.suggested_fix or f.level == "INFO"


def test_input_files_not_modified(scenario, tmp_path):
    import hashlib

    sc = scenario("clean_pass")
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (sc.approved, sc.export, sc.jira, sc.baseline)}
    run_scenario(sc, tmp_path)
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before}


def test_test_scenarios_generated(scenario, tmp_path):
    o = run_scenario(scenario("factor_mismatch"), tmp_path)
    t = o.result.tests
    assert t.parameter and {r["test_id"] for r in t.transaction} == {f"TX-0{i}" for i in range(1, 9)}
    # boundary tests carry an expected factor computed from the approved table
    assert any("1.045" in r["expected_factor"] for r in t.parameter)
    ids = [r["test_id"] for r in t.parameter]
    assert len(ids) == len(set(ids))


def test_no_baseline_default_passes_but_every_report_says_scope_check_not_performed(scenario, tmp_path):      # D-024 (2)
    line = "Scope check (tables outside approved/Jira): not performed – no baseline provided"
    sc = scenario("no_baseline_default_pass")
    o = run_scenario(sc, tmp_path, write_reports=True)
    assert o.verdict == "PASS" and o.exit_code == 0 and not [f for f in o.result.findings if f.rule_id == "NOT_EVALUATED_NO_BASELINE"]
    out = tmp_path / sc.name
    assert line in (out / "verdict.txt").read_text(encoding="utf-8")
    assert line in (out / "jira_comment.txt").read_text(encoding="utf-8")
    assert line in (out / "reconciliation_report.html").read_text(encoding="utf-8")
    data = json.loads((out / "reconciliation_report.json").read_text(encoding="utf-8"))
    assert data["summary"]["scope_check"] == line and data["summary"]["scope_check_performed"] is False
    import openpyxl

    ws = openpyxl.load_workbook(out / "reconciliation_report.xlsx")["Summary"]
    assert any(r[1] == line for r in ws.iter_rows(values_only=True))


def test_scope_check_line_when_baseline_given(scenario, tmp_path):
    o = run_scenario(scenario("clean_pass"), tmp_path)
    assert o.result.summary["scope_check_performed"] and "performed against" in o.result.summary["scope_check"]


def test_out_of_scope_table_dated_details(scenario, tmp_path):                                              # D-024 (3)
    o = run_scenario(scenario("out_of_scope_table_dated"), tmp_path, write_reports=True)
    hits = [f for f in o.result.findings if f.rule_id == "OUT_OF_SCOPE_TABLE_DATED"]
    assert hits and all(f.level == "NEEDS_REVIEW" and f.table_id and f.explanation and f.suggested_fix for f in hits)
    approved_ids = {p.id for p in o.result.mapping.pairs if p.approved is not None}
    assert not ({f.table_id for f in hits} & approved_ids)                                                  # only tables outside approved/Jira
    notes = json.loads((tmp_path / "out_of_scope_table_dated" / "run_manifest.json").read_text(encoding="utf-8"))["check_notes"]
    assert any("flagged" in n for n in notes["out_of_scope_table_dated"])


def test_out_of_scope_check_skips_silently_without_per_table_stamps(scenario, tmp_path):
    o = run_scenario(scenario("clean_pass"), tmp_path, write_reports=True)          # every table carries the same stamp
    assert not [f for f in o.result.findings if f.rule_id == "OUT_OF_SCOPE_TABLE_DATED"]
    assert any("check skipped" in n for n in o.result.check_notes["out_of_scope_table_dated"])


def test_run_with_stdout_redirected_to_a_file_does_not_crash(scenario, tmp_path):                           # D-025
    """A real subprocess with stdout in a file (cp1252 console code page on Windows): no UnicodeEncodeError, UTF-8 output."""
    import os
    import subprocess
    import sys

    sc = scenario("clean_pass")
    log = tmp_path / "stdout.txt"
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "RGL_DATA_DIR": str(tmp_path / "d")}
    env.pop("PYTHONUTF8", None)
    with log.open("wb") as fh:
        proc = subprocess.run([sys.executable, "-m", "rateguard_lite.cli", *sc.cli_args(tmp_path / "o"), "--baseline", str(sc.baseline)],
                              stdout=fh, stderr=subprocess.PIPE, env=env, check=False)    # noqa: S603
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    assert b"UnicodeEncodeError" not in proc.stderr and b"Internal error" not in log.read_bytes()
    assert b"PASS" in log.read_bytes()
