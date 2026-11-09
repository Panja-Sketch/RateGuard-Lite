"""D-029 (table identity), D-030 (grouping and diagnosis), D-031 (noise) - unit level."""

from dataclasses import replace
from pathlib import Path

import openpyxl
import pytest

from rateguard_lite.config import load_config
from rateguard_lite.engines.checks import integrity_findings
from rateguard_lite.engines.diagnose import build_problems, headline_counts
from rateguard_lite.engines.factors import compare_pair, emit_row_findings
from rateguard_lite.errors import UnsafeInputError
from rateguard_lite.ingest.approved import ingest_approved
from rateguard_lite.mapping.identity import clean_name, extract_number
from rateguard_lite.mapping.tables import TablePair
from tests.helpers import CFG, make_table


# ---- D-029
@pytest.mark.parametrize("raw,number", [
    ("TAB 701 - Roofing Surcharge", "701"), ("Copy of 703 Wind Relativity", "703"), ("702_Sprinkler_Allowance", "702"),
    ("Theft Device Credit (704)", "704"), ("12 Rates - Copy", "12"), ("Sheet 9 Base 3-15-2027 v2", "9"), ("Rates (2)", None),
    ("Table 5 Foo", "5"), ("Rates 12 vs 13", None),
])
def test_tab_name_number_extracted_from_anywhere(raw, number):
    assert extract_number(raw, strict=True, anywhere=True) == number


def test_clean_name_strips_noise():
    assert clean_name("TAB 701 - Roofing Surcharge") == "701 - Roofing Surcharge"
    assert clean_name("706 burglary alarm discount - FINAL") == "706 burglary alarm discount"
    assert clean_name("Copy of Base Rate (2)") == "Base Rate"


# ---- D-030
def _problems(a_rows, e_rows, baseline_rows=None):
    a = make_table(a_rows, "APPROVED")
    e = make_table(e_rows, "EXPORT")
    p = TablePair("T:1", "T", a, e, None, "CONFIRMED")
    if baseline_rows is not None:
        p.baseline = make_table(baseline_rows, "BASELINE")
    fr = compare_pair(p, CFG)
    emit_row_findings(fr, p)
    return build_problems(fr.findings, [p], {p.id: fr}), fr


def test_key_format_difference_is_one_grouped_item_with_cause():
    probs, fr = _problems([("100k", 1.1), ("300k", 1.2), ("1M", 1.3)], [("100000", 1.1), ("300000", 1.2), ("1000000", 1.3)])
    assert len(fr.findings) == 6 and len(probs) == 1 and probs[0].count == 6          # cell-level findings stay
    assert probs[0].cause_kind == "KEY_FORMAT" and "written differently" in probs[0].likely_cause


def test_typo_label_detected():
    probs, _ = _problems([("ccp_104", 1.1), ("ccp_105", 1.2), ("ccp_106", 1.3)], [("ccp_104", 1.1), ("ccpl_105", 1.2), ("ccp_106", 1.3)])
    assert probs[0].cause_kind == "KEY_TYPO"


def test_no_pattern_means_no_guess():
    probs, _ = _problems([("A", 1.1), ("B", 1.2), ("C", 1.3)], [("A", 1.5), ("B", 1.2), ("C", 1.9)])
    assert probs[0].cause_kind == "VALUES_DIFFER" and "No common pattern" in probs[0].likely_cause


def test_change_not_loaded_when_values_equal_baseline():
    probs, _ = _problems([("A", 1.1), ("B", 1.2), ("C", 1.3)], [("A", 1.0), ("B", 1.0), ("C", 1.0)], [("A", 1.0), ("B", 1.0), ("C", 1.0)])
    assert probs[0].cause_kind == "NOT_LOADED"


def test_copied_from_another_approved_table():
    a1 = make_table([(k, v) for k, v in zip("ABCDE", (1.1, 1.2, 1.3, 1.4, 1.5), strict=True)], "APPROVED")
    a2 = make_table([(k, v) for k, v in zip("ABCDE", (0.5, 0.6, 0.7, 0.8, 0.9), strict=True)], "APPROVED")
    e1 = make_table([(k, v) for k, v in zip("ABCDE", (1.1, 1.2, 1.3, 1.4, 1.5), strict=True)], "EXPORT")
    e2 = make_table([(k, v) for k, v in zip("ABCDE", (1.1, 1.2, 1.3, 1.4, 1.5), strict=True)], "EXPORT")       # table 2 holds table 1's numbers
    p1 = TablePair("T:1", "One", replace(a1, display_name="One"), e1, None, "CONFIRMED")
    p2 = TablePair("T:2", "Two", replace(a2, display_name="Two"), e2, None, "CONFIRMED")
    frs, findings = {}, []
    for p in (p1, p2):
        fr = compare_pair(p, CFG)
        frs[p.id] = fr
        findings += fr.findings
    probs = build_problems(findings, [p1, p2], frs)
    assert len(probs) == 1 and probs[0].table_id == "T:2" and probs[0].cause_kind == "COPIED_FROM" and "One" in probs[0].likely_cause


def test_headline_counts_tables_not_cells():
    probs, _ = _problems([("A", 1.1), ("B", 1.2)], [("A", 1.5), ("B", 1.9)])
    h = headline_counts(probs)
    assert h["problem_tables"] == 1 and h["problem_cells"] == 2 and h["headline"] == "1 table has problems (2 cells)."


# ---- D-031
def _zero_flag_table(origin, values):
    t = make_table([(v, 1.0) for v in values], origin)
    rows = tuple(replace(r, key=tuple(replace(c, flags=frozenset({"POSSIBLE_LEADING_ZERO_LOSS"})) for c in r.key)) for r in t.rows)
    return replace(t, rows=rows)


def test_leading_zero_warning_suppressed_for_plain_integers():
    a = _zero_flag_table("APPROVED", ["1", "2", "10"])
    e = make_table([("1", 1.0), ("2", 1.0), ("10", 1.0)], "EXPORT")
    assert not [f for f in integrity_findings(a, "T:1", "T", "approved", CFG, e) if f.rule_id == "POSSIBLE_LEADING_ZERO_LOSS"]
    e_padded = make_table([("01", 1.0), ("02", 1.0), ("10", 1.0)], "EXPORT")
    assert [f for f in integrity_findings(a, "T:1", "T", "approved", CFG, e_padded) if f.rule_id == "POSSIBLE_LEADING_ZERO_LOSS"]
    assert [f for f in integrity_findings(a, "T:1", "T", "approved", CFG) if f.rule_id == "POSSIBLE_LEADING_ZERO_LOSS"]      # no export to compare: keep warning


def test_header_only_tab_is_one_plain_input_error(tmp_path: Path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "5 Good Table"
    ws.append(["Territory", "Factor"])
    ws.append(["001", 1.1])
    ws2 = wb.create_sheet("6 Empty Table")
    ws2.append(["Territory", "Factor"])
    p = tmp_path / "approved.xlsx"
    wb.save(p)
    with pytest.raises(UnsafeInputError) as ei:
        ingest_approved(p, load_config(), "BOP")
    text = ei.value.render()
    assert "headers but no rows" in text and "6 Empty Table" in text


def test_scope_config_default():
    assert load_config().scope.oos_max_stamp_share == 0.30
    assert load_config().scope.oos_enabled is False            # D-032
    assert load_config(profile="oos_stamp_wide").scope.oos_enabled is True


def test_cli_version_flag():
    from typer.testing import CliRunner

    from rateguard_lite import __version__
    from rateguard_lite.cli import app
    r = CliRunner().invoke(app, ["--version"])
    assert r.exit_code == 0 and f"RateGuard-Lite {__version__}" in r.output and __version__ == "1.1.0"
