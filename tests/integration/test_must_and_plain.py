"""D-026 (Must Test list) and D-027 (plain language) checked on the real sample scenarios and report files."""

import html
import json
import re
from pathlib import Path

import openpyxl
import pytest

from rateguard_lite.config import LimitsCfg, aux
from rateguard_lite.engines.common import make_finding
from rateguard_lite.errors import UnsafeInputError
from rateguard_lite.guard.safety import safety_gate_workbook
from rateguard_lite.model.findings import GATE_TEXT, Severity, Status, Verdict
from rateguard_lite.plain import BANNED_WORDS, NO_BASELINE_LINE, RULE_ID_RE
from rateguard_lite.report.writer import FULL_LIST_LINE
from rateguard_lite.samples.generator import SCENARIOS
from rateguard_lite.samples.runner import run_scenario

SRC = Path(__file__).resolve().parents[2] / "src" / "rateguard_lite"


# ------------------------------------------------------------------ D-026
@pytest.fixture(scope="module")
def outcomes(scenario, tmp_path_factory):
    base = tmp_path_factory.mktemp("allruns")
    runs = {n: run_scenario(scenario(n, 12), base, write_reports=(n in ("factor_mismatch", "clean_pass"))) for n in SCENARIOS}
    return {n: o for n, o in runs.items() if o.result is not None}       # input-rejected scenarios (exit 3) never reach the report stage


def test_every_scenario_gets_between_three_and_eight_must_tests(outcomes):               # D-028: default cap 8, fix retests exempt
    for name, o in outcomes.items():
        n = len(o.result.tests.must)
        retests = len([x for x in o.result.tests.must if x["category"] == "C10"])
        assert 3 <= n <= max(8, retests), (name, n)
        assert all(x["score"] >= 20 for x in o.result.tests.must), name
        assert [x["priority"] for x in o.result.tests.must] == list(range(1, n + 1))


def test_filing_date_test_present_and_fix_retests_first(outcomes):
    for name, o in outcomes.items():
        cats = [x["category"] for x in o.result.tests.must]
        assert any(c.startswith("C1") for c in cats), name
    o = outcomes["factor_mismatch"]
    must = o.result.tests.must
    assert must[0]["category"] == "C10" and "exact row that was wrong" in must[0]["what"]
    mismatched = {f.table_id for f in o.result.findings if f.rule_id == "VALUE_MISMATCH"}
    assert {x["table"] for x in must if x["category"] == "C10"} >= {p.display for p in o.result.mapping.pairs if p.id in mismatched}


def test_must_list_is_deterministic(scenario, tmp_path):
    a = run_scenario(scenario("factor_mismatch", 12), tmp_path / "a").result.tests.must
    b = run_scenario(scenario("factor_mismatch", 12), tmp_path / "b").result.tests.must
    assert a == b


def test_default_files_show_must_tests_and_all_candidates_sheet(outcomes, tmp_path_factory):
    o = outcomes["factor_mismatch"]
    out = o.result.out
    assert out is not None
    for f in ("verdict.txt", "jira_comment.txt", "reconciliation_report.html"):
        text = html.unescape((out / f).read_text(encoding="utf-8"))
        assert "Must Test" in text or "MUST TEST" in text, f
        assert FULL_LIST_LINE in text, f
    wb = openpyxl.load_workbook(out / "test_scenarios.xlsx")
    assert wb.sheetnames[:2] == ["Must Test", "All Candidates"] and "Parameter Tests" not in wb.sheetnames
    rows = list(wb["Must Test"].iter_rows(values_only=True))
    assert rows[0] == ("Priority", "Test ID", "What to test", "Why this matters", "How to set up the quote", "What you should see", "Source", "Category")
    assert len(rows) - 1 == len(o.result.tests.must)
    assert wb["All Candidates"].max_row - 1 >= len(o.result.tests.must)
    data = json.loads((out / "reconciliation_report.json").read_text(encoding="utf-8"))
    assert data["test_scenarios"]["must_test_count"] == len(o.result.tests.must) and data["test_scenarios"]["parameter_tests"] >= 1   # additive only


def test_tests_full_keeps_the_long_lists(scenario, tmp_path):
    from rateguard_lite.pipeline import RunOptions, run_reconciliation
    sc = scenario("factor_mismatch", 12)
    run_reconciliation(RunOptions(sc.approved, sc.jira, sc.export, tmp_path / "o", sc.baseline, user="t", tests="full"))
    wb = openpyxl.load_workbook(tmp_path / "o" / "test_scenarios.xlsx")
    assert {"Must Test", "All Candidates", "Parameter Tests", "Transaction Matrix"} <= set(wb.sheetnames)


# ------------------------------------------------------------------ D-027
def test_headlines_are_the_agreed_plain_words():
    assert GATE_TEXT[Verdict.PASS] == "All approved values match PolicyCenter. Ready for QA testing."
    assert GATE_TEXT[Verdict.FAIL] == "Some values in PolicyCenter don't match what was approved. Fix these before QA."
    assert GATE_TEXT[Verdict.BLOCKED] == "The tool couldn't finish checking. Some tables or files need your help first."
    assert GATE_TEXT[Verdict.NEEDS_REVIEW] == "Checking is done, but a few items need a person to look at them."
    assert GATE_TEXT[Verdict.PASS_WITH_WARNINGS] == "Values match, with a few things worth a quick look."
    assert NO_BASELINE_LINE == "We did not check tables outside this change, because no 'before' export was provided."


def test_rendered_value_mismatch_snapshot():
    f = make_finding("VALUE_MISMATCH", severity=Severity.CRITICAL, table_id="T1", table_name="Number of Stories", key="Stories=5", column="Stories Factor",
                     approved_value="1.0600", export_value="1.0350", approved_ref="a.xlsx!Stories!C5", message="approved 1.0600 vs export 1.0350")
    assert f.plain_what == "Table 'Number of Stories' (Stories = 5): the Stories Factor value in PolicyCenter is not the approved value. Approved 1.0600, PolicyCenter has 1.0350 (2.4% lower)."
    assert f.plain_why == "Policies with Stories = 5 will be undercharged."
    assert f.plain_action == "Change the value in PolicyCenter to 1.0600 and export the rate book again."
    assert f.explanation.startswith(f.plain_what) and f.suggested_fix == f.plain_action


def test_rendered_other_snapshots():
    h = make_finding("EXTRA_ROW_IN_EXPORT", severity=Severity.HIGH, table_name="Territory", key="Territory=9", message="x")
    assert h.plain_what == "Table 'Territory' (Territory = 9) is in PolicyCenter but is not in the approved file."
    assert h.plain_action == "Remove the extra row, or ask the actuary whether the approved file is out of date."
    o = make_finding("VALUE_MISMATCH", severity=Severity.CRITICAL, table_name="T", approved_value="1.00", export_value="1.10", message="x")
    assert "10.0% higher" in o.plain_what and o.plain_why.endswith("will be overcharged.")
    n = make_finding("NOT_EVALUATED_NO_BASELINE", status=Status.NOT_EVALUATED, message="x")
    assert n.plain_what == NO_BASELINE_LINE


def test_every_message_has_three_plain_parts_and_no_jargon():
    msgs = aux("messages")
    assert msgs
    for rule, m in msgs.items():
        for part in ("what", "why", "action"):
            assert m.get(part, "").strip(), (rule, part)
            text = m[part]
            assert not RULE_ID_RE.search(text), (rule, part, text)
            for w in BANNED_WORDS:
                assert w.lower() not in text.lower(), (rule, part, w)


def test_every_rule_used_in_code_has_a_message():
    msgs = aux("messages")
    used = set()
    for py in SRC.rglob("*.py"):
        used |= set(re.findall(r'make_finding\(\s*"([A-Z_]+)"', py.read_text(encoding="utf-8")))
    assert used and not (used - set(msgs)), used - set(msgs)


def test_every_finding_in_every_scenario_is_plain_and_complete(outcomes):
    seen = set()
    for name, o in outcomes.items():
        for f in o.result.findings:
            seen.add(f.rule_id)
            assert f.plain_what and f.plain_why and f.plain_action, (name, f.rule_id)
            for text in (f.plain_what, f.plain_why, f.plain_action):
                assert not RULE_ID_RE.search(text), (name, f.rule_id, text)
                for w in BANNED_WORDS:
                    assert w.lower() not in text.lower(), (name, f.rule_id, w, text)
            assert f.message, "technical detail is kept"
    assert {"VALUE_MISMATCH", "OUT_OF_SCOPE_TABLE_DATED"} <= seen


def test_reports_keep_technical_detail_apart(outcomes):
    o = outcomes["factor_mismatch"]
    out = o.result.out
    assert out is not None
    html = (out / "reconciliation_report.html").read_text(encoding="utf-8")
    assert "How to read this report" in html and "Technical details" in html and "<details class=\"tech\">" in html
    assert "VALUE_MISMATCH" in html                                         # present, but only inside the folded technical details
    main = re.sub(r'<details class="tech">.*?</details>', "", html, flags=re.S)
    assert "VALUE_MISMATCH" not in main
    ws = openpyxl.load_workbook(out / "reconciliation_report.xlsx")["Discrepancies"]
    head = [c.value for c in ws[1]]
    assert head == ["Level", "Table", "Where in the table", "What we found", "Why it matters", "What to do", "Technical details"]
    assert "Rule: VALUE_MISMATCH" in str(ws.cell(row=2, column=7).value)
    txt = (out / "verdict.txt").read_text(encoding="utf-8")
    assert "VALUE_MISMATCH" not in txt
    data = json.loads((out / "reconciliation_report.json").read_text(encoding="utf-8"))
    f0 = data["findings"][0]
    assert f0["rule_id"] == "VALUE_MISMATCH" and f0["plain_what"] and f0["plain_why"] and f0["plain_action"] and f0["message"] and f0["explanation"]


# ---- plain input errors (B4)
def test_locked_file_message(tmp_path, monkeypatch):
    p = tmp_path / "export.xlsx"
    p.write_bytes(b"PK\x03\x04" + b"0" * 50)
    real = Path.open

    def locked(self, *a, **k):
        if self.name == "export.xlsx":
            raise PermissionError(13, "locked")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "open", locked)
    with pytest.raises(UnsafeInputError) as e:
        safety_gate_workbook(p, LimitsCfg())
    assert "because it's open in Excel" in e.value.what and e.value.next_step == "Close Excel and run again."


def test_wrong_type_password_and_missing_messages(tmp_path):
    csv = tmp_path / "a.csv"
    csv.write_text("x")
    with pytest.raises(UnsafeInputError, match="wrong file type"):
        safety_gate_workbook(csv, LimitsCfg())
    pw = tmp_path / "pw.xlsx"
    pw.write_bytes(b"\xd0\xcf\x11\xe0" + b"\0" * 50)
    with pytest.raises(UnsafeInputError, match="password-protected"):
        safety_gate_workbook(pw, LimitsCfg())
    with pytest.raises(UnsafeInputError, match="couldn't find"):
        safety_gate_workbook(tmp_path / "nope.xlsx", LimitsCfg())


def test_empty_sheet_and_missing_headers_are_explained(tmp_path):
    from rateguard_lite.config import load_config
    from rateguard_lite.engines.scope import _plain_reason
    from rateguard_lite.ingest.gw_export import ingest_export

    wb = openpyxl.Workbook()
    wb.active.title = "Empty Tab"
    wb.create_sheet("Notes").append(["just", "some", "words"])
    p = tmp_path / "export.xlsx"
    wb.save(p)
    res = ingest_export(p, load_config("default"), "EXPORT", None)
    reasons = {i.sheet: i.reason for i in res.issues}
    assert reasons["Empty Tab"] == "The tab is empty."
    assert _plain_reason(reasons["Empty Tab"]) == "The tab has nothing in it."
    assert _plain_reason("Could not find a header row.") == "We couldn't find the column headers."


def test_untriggered_control_tests_only_in_all_candidates(outcomes):                      # D-028
    o = outcomes["clean_pass"]
    cats = {x["category"] for x in o.result.tests.must}
    assert not cats & {"C4", "C9", "C11"}
    left_out = {x["category"]: x for x in o.result.tests.candidates if x["category"] in ("C4", "C9")}
    assert set(left_out) == {"C4", "C9"} and all(x["selected"] == "No" and "does not mention" in x["note"] for x in left_out.values())
    assert len(o.result.tests.must) <= 8


def test_story_mentions_switch_control_tests_on(scenario, tmp_path):
    from rateguard_lite.pipeline import RunOptions, run_reconciliation
    sc = scenario("clean_pass", 12)
    jira = tmp_path / "story.txt"
    jira.write_text(sc.jira.read_text(encoding="utf-8") + "\nAlso test endorsements, other states, and flat cancellation with fees.\n", encoding="utf-8")
    r = run_reconciliation(RunOptions(sc.approved, jira, sc.export, tmp_path / "o", sc.baseline, user="t"))
    cand = {x["category"]: x for x in r.tests.candidates if x["category"] in ("C4", "C9", "C11")}
    assert set(cand) == {"C4", "C9", "C11"}                                    # once mentioned they qualify (score 20+); the cap of 8 may still cut them
    assert all(x["score"] >= 20 and "does not mention" not in x["note"] for x in cand.values())


def test_range_keys_use_business_words():                                               # D-028
    from rateguard_lite.plain import pretty_key
    assert pretty_key("Stories From=4, Stories To=6") == "4 to 6 stories"
    assert pretty_key("Stories From=10, Stories To=(blank)") == "10 or more stories"
    assert pretty_key("Stories From=4, Stories To=4") == "4 stories"
    assert pretty_key("Territory=9, Stories From=4, Stories To=6") == "Territory = 9, 4 to 6 stories"
    f = make_finding("VALUE_MISMATCH", severity=Severity.CRITICAL, table_name="Stories", key="Stories From=4, Stories To=6", column="Stories Factor",
                     approved_value="1.06", export_value="1.03", message="x")
    assert "(4 to 6 stories)" in f.plain_what and f.plain_why == "Policies with 4 to 6 stories will be undercharged."
    assert "Stories From" not in f.plain_what + f.plain_why
