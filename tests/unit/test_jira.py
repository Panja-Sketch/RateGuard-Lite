from rateguard_lite.config import load_config
from rateguard_lite.ingest.jira import parse_jira, parse_jira_text, to_plain_text

CFG = load_config()


def parse(text):
    return parse_jira_text(to_plain_text(text, ".txt"), CFG)


def refs(scope):
    return [(r.table_number, r.table_name, r.action.value) for r in scope.tables]


def test_three_formats_parse_identically(scenario):
    sc = scenario("clean_pass")
    parsed = [parse_jira(sc.dir / f"jira.{e}", CFG) for e in ("txt", "md", "html")]
    base = parsed[0]
    assert base.ticket_key == "PC-12345" and base.lob == "BOP" and base.states == ("OR",)
    assert base.filing_effective_date == "2027-03-15" and base.renewal_effective_date == "2027-05-15"
    assert set(base.transaction_scope) == {"NB", "RENEWAL"}
    for other in parsed[1:]:
        assert refs(other) == refs(base)
        assert other.filing_effective_date == base.filing_effective_date and other.states == base.states


def test_bypass_via_strikethrough_and_keyword(scenario):
    s = parse_jira(scenario("clean_pass").dir / "jira.txt", CFG)
    t48 = next(r for r in s.tables if r.table_number == "48")
    assert t48.action.value == "BYPASS"
    assert next(r for r in s.tables if r.table_number == "10").action.value == "ADD"
    assert next(r for r in s.tables if r.table_name == "Territory Factor").action.value == "MODIFY"


def test_html_strikethrough_and_script_removed():
    html = "<html><body><p>LOB: BOP</p><ul><li><del>Table 7</del> something</li></ul><script>alert(1)</script></body></html>"
    text = to_plain_text(html, ".html")
    assert "~~Table 7~~" in text and "alert" not in text


def test_wiki_markup_strikethrough_not_dates():
    text = to_plain_text("Filing Effective Date: 2027-03-15\n-Table 9- retired\n- plain bullet", ".txt")
    assert "~~Table 9~~" in text and "2027-03-15" in text


def test_conflicting_actions_need_review():
    s = parse("Tables to update:\n- Table 5 Foo Bar\nBypass:\n- Table 5 Foo Bar\n")
    assert any("JIRA_CONFLICT" in f for f in s.review_flags)


def test_multiple_filing_dates_flagged():
    s = parse("Filing Effective Date: 03/15/2027\nFiling Effective Date: 04/01/2027\n")
    assert s.filing_effective_date is None and any("MULTIPLE_FILING_DATES" in f for f in s.review_flags)


def test_ambiguous_date_not_guessed():
    s = parse("Filing Effective Date: 3/15/27\n")
    assert s.filing_effective_date is None and s.unparsed_signals


def test_ca_is_ambiguous_without_context():
    s = parse("Please update CA rates next quarter.\n")
    assert s.lob is None and any("CA" in x for x in s.unparsed_signals)


def test_state_names_and_codes():
    s = parse("State: OR, WA\nLine of business: Businessowners\n")
    assert set(s.states) == {"OR", "WA"} and s.lob == "BOP"


def test_overrides_recorded(scenario):
    s = parse_jira(scenario("clean_pass").dir / "jira.txt", CFG, lob="ca", state="wa")
    assert s.lob == "CA" and s.states == ("WA",) and len(s.overrides) == 2


def test_unrecognised_table_line_is_reported_not_dropped():
    s = parse("The table situation is complicated and unclear here.\n")
    assert s.unparsed_signals
