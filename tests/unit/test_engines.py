import random
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from rateguard_lite.engines.factors import compare_pair, compare_values, emit_row_findings
from rateguard_lite.engines.ranges import boundary_shifts, check_ranges
from tests.helpers import CFG, make_table, pair


def run(a, e):
    p = pair(a, e)
    fr = compare_pair(p, CFG)
    fr.findings += boundary_shifts(p, fr)
    if fr.a_idx:
        emit_row_findings(fr, p)
    return fr


def rules(fr):
    return sorted(f.rule_id for f in fr.findings)


@pytest.mark.parametrize("a,e,exp", [
    ("1.0450", "1.0450", "MATCH"),
    ("1.04567", "1.0457", "MATCH_ROUNDED"),
    ("0.0123456", "0.0123", "PRECISION_TRUNCATION"),
    ("1.0450", "10.4500", "ORDER_OF_MAGNITUDE"),
    ("1.2345", "1.2435", "TRANSPOSITION"),
    ("1.0450", "-1.0450", "SIGN_FLIP"),
    ("1.0450", "1.0250", "VALUE_MISMATCH"),
    ("0", "1.0000", "VALUE_MISMATCH"),
])
def test_compare_values_rules(a, e, exp):
    assert compare_values(Decimal(a), Decimal(e), 4, CFG, Decimal(0))[0] == exp


def test_identical_tables_clean():
    rows = [("001", "1.1000"), ("002", "1.2000")]
    fr = run(make_table(rows), make_table(rows, "EXPORT"))
    assert fr.findings == [] and fr.stats["compared"] == 2


def test_row_order_never_matters():
    rows = [(f"{i:03d}", f"1.{i:04d}") for i in range(1, 30)]
    shuffled = rows[:]
    random.Random(3).shuffle(shuffled)
    assert run(make_table(rows), make_table(shuffled, "EXPORT")).findings == []


def test_missing_and_extra_rows():
    a = make_table([("001", "1.0000"), ("002", "1.1000")])
    e = make_table([("001", "1.0000"), ("003", "1.2000")], "EXPORT")
    assert rules(run(a, e)) == ["EXTRA_ROW_IN_EXPORT", "MISSING_ROW_IN_EXPORT"]


def test_wildcard_vs_concrete_is_wildcard_mismatch():
    a = make_table([("*", "1.0000"), ("001", "1.1000")])
    e = make_table([("999", "1.0000"), ("001", "1.1000")], "EXPORT")
    assert rules(run(a, e)) == ["WILDCARD_MISMATCH"]


def test_blank_export_factor_is_critical():
    a = make_table([("001", "1.0000")])
    e = make_table([("001", None)], "EXPORT")
    e.rows[0].factors["factor"] = type(e.rows[0].factors["factor"])("BLANK", None, None, "B2")
    fr = run(a, e)
    assert [f.level for f in fr.findings] == ["CRITICAL"] and fr.findings[0].rule_id == "BLANK_FACTOR"


def test_different_key_columns_need_mapping():
    a = make_table([("001", "1.0")], keys=("territory",))
    e = make_table([("001", "1.0")], "EXPORT", keys=("zone",))
    assert [f.level for f in run(a, e).findings] == ["NEEDS_MAPPING"]


# ---- A2: any single mutated factor is detected (>= 500 cases) and yields exactly one CRITICAL ----
@settings(max_examples=600, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(st.integers(1000, 30000), min_size=2, max_size=25, unique=True), st.data())
def test_single_cell_mutation_always_detected(units, data):
    rows = [(f"{i:03d}", f"{Decimal(u) / 10000:.4f}") for i, u in enumerate(units)]
    idx = data.draw(st.integers(0, len(rows) - 1))
    delta = data.draw(st.integers(-9000, 9000).filter(lambda d: d != 0))
    mutated = [list(r) for r in rows]
    new = Decimal(rows[idx][1]) + Decimal(delta) / 10000
    mutated[idx][1] = f"{new:.4f}"
    fr = run(make_table(rows), make_table(mutated, "EXPORT"))
    crit = [f for f in fr.findings if f.level == "CRITICAL"]
    assert len(crit) == 1, (rows[idx], mutated[idx])
    assert len(fr.findings) == 1


@settings(max_examples=60, deadline=None)
@given(st.lists(st.integers(1000, 30000), min_size=2, max_size=40, unique=True), st.randoms(use_true_random=False))
def test_shuffle_invariance_and_identical_pass(units, rnd):
    rows = [(f"{i:03d}", f"{Decimal(u) / 10000:.4f}") for i, u in enumerate(units)]
    sh = rows[:]
    rnd.shuffle(sh)
    assert run(make_table(rows), make_table(sh, "EXPORT")).findings == []


# ---- range integrity ----
def rng(rows):
    return make_table(rows, "EXPORT", keys=("stories_from", "stories_to"))


def test_range_clean_gap_overlap_and_inverted():
    assert check_ranges(rng([(1, 3, "1"), (4, 6, "1"), (7, "*", "1")]), "T", "T", "export") == []
    gap = check_ranges(rng([(1, 3, "1"), (5, 6, "1")]), "T", "T", "export")
    assert [f.rule_id for f in gap] == ["RANGE_GAP"]
    ov = check_ranges(rng([(1, 4, "1"), (4, 6, "1")]), "T", "T", "export")
    assert [f.rule_id for f in ov] == ["RANGE_OVERLAP"]
    bad = check_ranges(rng([(9, 3, "1")]), "T", "T", "export")
    assert bad[0].rule_id == "RANGE_MIN_GT_MAX" and bad[0].level == "CRITICAL"


def test_off_by_one_boundary_detected():
    a = make_table([(4, 6, "1.0450"), (7, 10, "1.1")], keys=("stories_from", "stories_to"))
    e = make_table([(4, 7, "1.0450"), (8, 10, "1.1")], "EXPORT", keys=("stories_from", "stories_to"))
    assert rules(run(a, e)).count("OFF_BY_ONE_BOUNDARY") == 2
