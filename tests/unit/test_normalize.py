from datetime import date, datetime
from decimal import Decimal

import pytest

from rateguard_lite.ingest.normalize import AmbiguousDateError, canonical_colname, fmt_decimal, infer_type, normalize_cell, parse_date, quantize, to_decimal
from rateguard_lite.model.core import ColumnRole, ColumnType

TOK = frozenset({"", "*", "ALL"})


def nc(raw, role=ColumnRole.KEY, ctype=ColumnType.STRING, **kw):
    return normalize_cell(raw, role=role, ctype=ctype, coord="A1", tokens=TOK, **kw)


def test_float_uses_repr_not_binary_noise():
    assert to_decimal(0.1 + 0.2) == Decimal("0.30000000000000004")      # repr text, never float arithmetic
    assert to_decimal(1.045) == Decimal("1.045")


@pytest.mark.parametrize("raw,exp", [("$1,234.50", "1234.5"), ("(12)", "-12"), ("1.0450", "1.045"), (3, "3"), (0.0, "0")])
def test_decimal_text_forms(raw, exp):
    assert fmt_decimal(to_decimal(raw)) == exp


def test_scientific_notation_rejected_unless_allowed():
    assert to_decimal("1e5") is None
    assert to_decimal("1e5", allow_sci=True) == Decimal("1E+5")


def test_percent_only_divides_when_configured():
    assert to_decimal("5%") == Decimal("5")
    assert to_decimal("5%", percent=True) == Decimal("0.05")


def test_quantize_half_up():
    assert quantize(Decimal("1.00005"), 4) == Decimal("1.0001")
    assert quantize(Decimal("1.00004"), 4) == Decimal("1.0000")


@pytest.mark.parametrize("raw,iso", [("3/15/2027", "2027-03-15"), ("03-15-2027", "2027-03-15"), ("2027-03-15 00:00:00", "2027-03-15"),
                                     ("March 15, 2027", "2027-03-15"), ("Mar 15 2027", "2027-03-15"), (date(2027, 3, 15), "2027-03-15"),
                                     (datetime(2027, 3, 15), "2027-03-15")])
def test_dates_us_default(raw, iso):
    assert parse_date(raw)[0] == iso


def test_date_with_time_is_flagged():
    assert parse_date("2027-03-15 10:30:00") == ("2027-03-15", True)


@pytest.mark.parametrize("raw", ["3/15/27", "15/3/2027", "tomorrow", ""])
def test_ambiguous_dates_are_never_guessed(raw):
    with pytest.raises(AmbiguousDateError):
        parse_date(raw)


def test_wildcards_and_blanks():
    assert nc("*").kind == "WILDCARD" and nc("").kind == "WILDCARD" and nc(None).kind == "WILDCARD"
    f = nc(None, role=ColumnRole.FACTOR, ctype=ColumnType.DECIMAL)
    assert f.kind == "BLANK"          # a blank factor is always an issue, never a wildcard


def test_minus_one_is_not_a_global_wildcard():
    assert nc("-1").kind == "VALUE"
    assert normalize_cell("-1", role=ColumnRole.KEY, ctype=ColumnType.STRING, coord="A1", tokens=TOK | {"-1"}).kind == "WILDCARD"


def test_leading_zeros_preserved_and_restored():
    assert nc("0042").value == "0042"
    assert nc(42, number_format="0000").value == "0042"
    assert "POSSIBLE_LEADING_ZERO_LOSS" in nc(42).flags


def test_excel_errors_flagged():
    assert "EXCEL_ERROR" in nc("#REF!").flags


def test_boolean_tokens():
    assert nc("Y", ctype=ColumnType.BOOLEAN).value == "TRUE" and nc("no", ctype=ColumnType.BOOLEAN).value == "FALSE"


def test_numeric_string_key_matches_numeric_cell():
    assert nc("12").value == nc(12).value == "12"
    assert nc("12.0").value == "12"


def test_column_synonyms():
    assert canonical_colname("Terr") == "territory"
    assert canonical_colname("Stories From") == "number_of_stories_from"
    assert canonical_colname("Class Code") == "class_code"


def test_type_inference():
    assert infer_type([1, 2, 3]) == ColumnType.INTEGER
    assert infer_type([1.5, 2]) == ColumnType.DECIMAL
    assert infer_type(["A", 1]) == ColumnType.STRING
    assert infer_type(["001", "002"]) == ColumnType.STRING
