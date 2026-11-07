"""D-026: risk-based Must Test selection (pure logic; no files)."""

from rateguard_lite.tests_gen.must import HARD_CAP, Cand, _combine, _tid, select


def cand(cat: str, score: float, table: str = "", name: str = "", named: bool = False) -> Cand:
    return Cand(id=_tid("MT", cat, table, name or str(score)), cats=[cat], score=score, table_id=table, table=table, what="w", why="y", setup="s",
                expect="e", source="src", named=named)


def pool() -> list[Cand]:
    c = [cand("C1", 30, name="before"), cand("C1", 30, name="on"), cand("C3", 25), cand("C9", 12), cand("C4", 12)]
    c += [cand("C7", 20 + i, table=f"T{i}") for i in range(10)]
    return c


def test_score_ranks_higher_risk_first():
    chosen = select(pool(), 12)
    scores = [c.score for c in chosen]
    assert scores == sorted(scores, reverse=True)


def test_cap_is_respected_and_hard_capped_at_twenty():
    assert len(select(pool(), 12)) == 12
    assert len(select(pool(), 6)) == 6
    big = [cand("C5", 20.0 + i, table=f"T{i}") for i in range(40)] + [cand("C1", 30, name="on")]
    assert len(select(big, 20)) == 20 and HARD_CAP == 20


def test_never_more_than_two_tests_per_table_except_fix_retests():
    c = [cand("C1", 30, name="on")] + [cand(cat, 20 - i, table="T1", name=cat) for i, cat in enumerate(["C5", "C6", "C7", "C8"])]
    c += [cand("C10", 40, table="T1", name="a"), cand("C10", 40, table="T1", name="b")]
    chosen = select(c, 12)
    per = [x for x in chosen if x.table_id == "T1" and x.cats[0] != "C10"]
    assert len(per) <= 2
    assert len([x for x in chosen if x.cats[0] == "C10"]) == 2          # fix retests are exempt


def test_fix_retests_always_included_and_ranked_first():
    c = pool() + [cand("C10", 40, table="T3", name="fix"), cand("C10", 40, table="T4", name="fix2")]
    chosen = select(c, 3)
    assert [x.cats[0] for x in chosen[:2]] == ["C10", "C10"]
    assert any(x.cats[0] == "C1" for x in chosen)                         # the date test is kept even when the list is tiny


def test_a_filing_date_test_is_always_present():
    low = [cand("C5", 90, table=f"T{i}") for i in range(30)] + [cand("C1", 20, name="on")]
    assert any(x.cats[0] == "C1" for x in select(low, 3))


def test_candidates_scoring_below_twenty_are_dropped_not_padded():           # D-028
    c = [cand("C1", 30, name="on"), cand("C3", 25), cand("C5", 15, table="T1"), cand("C7", 19.9, table="T2")]
    chosen = select(c, 8)
    assert len(chosen) == 2 and all(x.score >= 20 for x in chosen)
    assert {x.note for x in c if x.state == "cut"} == {"Score below 20."}


def test_untriggered_control_tests_stay_out_of_the_list():                    # D-028
    skipped = cand("C9", 20)
    skipped.excluded = "The story does not mention other states or lines."
    c = [cand("C1", 30, name="on"), cand("C3", 25), skipped]
    assert skipped not in select(c, 8) and skipped.state == "cut" and "does not mention" in skipped.note


def test_combining_one_quote_covers_two_things():
    on = cand("C1", 30, name="on")
    on.id = _tid("MT", "C1", "on")
    on.setup = "New business, effective 3/15/2027."
    on.expect = "The rating worksheet should use the new rates (rate book X)."
    donor = cand("C6", 40, table="OR Stories", name="edge")
    donor.expect = "In the rating worksheet, the Stories Factor for OR Stories should be 1.06."
    cands = [on, donor, cand("C5", 20, table="Other")]
    _combine(cands)
    assert on.cats == ["C1", "C6"] and donor.state == "combined" and "Combines" in on.note
    chosen = select(cands, 12)
    assert donor not in chosen and on in chosen


def test_selection_is_deterministic():
    a = [c.id for c in select(pool(), 12)]
    b = [c.id for c in select(list(reversed(pool())), 12)]
    assert a == b
    assert _tid("MT", "C1", "on") == _tid("MT", "C1", "on")
