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
