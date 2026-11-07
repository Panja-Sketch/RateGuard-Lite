"""Value, date, decimal and column-name normalization (spec 5.4-5.6)."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal

from ..config import aux
from ..model.core import CellValue, ColumnRole, ColumnType

EXCEL_ERRORS = {"#N/A", "#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#NULL!", "#NUM!"}
_NUM_RE = re.compile(r"^[+-]?\d+(\.\d+)?$")
_MONTHS = ("jan feb mar apr may jun jul aug sep oct nov dec").split()


class AmbiguousDateError(ValueError):
    pass


# ---------------------------------------------------------------- strings / names
def norm_string(s: str, upper: bool = True) -> str:
    s = unicodedata.normalize("NFKC", s).strip()
    s = re.sub(r"\s+", " ", s)
    return s.upper() if upper else s


def norm_colname(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s)).strip().lower()
    s = re.sub(r"[\s_\-]+", "_", s)
    s = re.sub(r"[^\w]", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


_RANGE_SUFFIXES = ("_min", "_max", "_from", "_to", "_low", "_high", "_lower", "_upper", "_minimum", "_maximum")


def canonical_colname(s: str, extra_synonyms: dict[str, str] | None = None) -> str:
    """Normalize, then apply the synonym map (also to the base of range columns)."""
    syn: dict[str, str] = dict(aux("synonyms").get("column_synonyms", {}))
    if extra_synonyms:
        syn.update(extra_synonyms)
    n = norm_colname(s)
    if n in syn:
        return syn[n]
    for suf in _RANGE_SUFFIXES:
        if n.endswith(suf) and n[: -len(suf)] in syn:
            return syn[n[: -len(suf)]] + suf
    return n


# ---------------------------------------------------------------- decimals
def fmt_decimal(d: Decimal) -> str:
    if d == 0:
        return "0"
    d = d.normalize()
    s = format(d, "f")
    return s


def to_decimal(raw: Any, allow_sci: bool = False, percent: bool = False) -> Decimal | None:
    """Decimal from the cell text representation; never float arithmetic."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, Decimal):
        d = raw
    elif isinstance(raw, int):
        d = Decimal(raw)
    elif isinstance(raw, float):
        d = Decimal(repr(raw))
    else:
        s = str(raw).strip()
        if not s:
            return None
        pct = s.endswith("%")
        s = s.replace(",", "").replace("$", "").replace("%", "").strip()
        neg = s.startswith("(") and s.endswith(")")
        if neg:
            s = s[1:-1]
        if not allow_sci and re.search(r"\d[eE][+-]?\d", s):
            return None
        try:
            d = Decimal(s)
        except InvalidOperation:
            return None
        if neg:
            d = -d
        if pct and percent:
            d = d / 100
        return d if d.is_finite() else None
    if not d.is_finite():
        return None
    if percent:
        d = d / 100
    return d


def quantize(d: Decimal, scale: int) -> Decimal:
    return d.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)


def decimal_scale_from_format(fmt: str | None) -> int | None:
    """Digits after the decimal point in an Excel number format like 0.0000."""
    if not fmt or fmt == "General":
        return None
    m = re.search(r"0\.(0+)", fmt)
    return len(m.group(1)) if m else None


# ---------------------------------------------------------------- dates
def parse_date(raw: Any, order: str = "US_MDY") -> tuple[str, bool]:
    """-> (ISO date, has_nonzero_time). Raises AmbiguousDateError."""
    if isinstance(raw, datetime):
        return raw.date().isoformat(), (raw.hour, raw.minute, raw.second, raw.microsecond) != (0, 0, 0, 0)
    if isinstance(raw, date):
        return raw.isoformat(), False
    s = str(raw).strip()
    if not s:
        raise AmbiguousDateError("empty date")
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?)?$", s)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
        try:
            iso = date(y, mo, d).isoformat()
        except ValueError as exc:
            raise AmbiguousDateError(s) from exc
        t = any(int(x or 0) for x in (m[4], m[5], m[6]))
        return iso, t
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", s)
    if m:
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        mo, d = (a, b) if order == "US_MDY" else (b, a)
        try:
            return date(y, mo, d).isoformat(), False
        except ValueError as exc:
            raise AmbiguousDateError(f"{s} is not a valid date under {order}") from exc
    m = re.match(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})$", s)
    if m and m[1][:3].lower() in _MONTHS:
        try:
            return date(int(m[3]), _MONTHS.index(m[1][:3].lower()) + 1, int(m[2])).isoformat(), False
        except ValueError as exc:
            raise AmbiguousDateError(s) from exc
    m = re.match(r"^(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})$", s)
    if m and m[2][:3].lower() in _MONTHS:
        try:
            return date(int(m[3]), _MONTHS.index(m[2][:3].lower()) + 1, int(m[1])).isoformat(), False
        except ValueError as exc:
            raise AmbiguousDateError(s) from exc
    # Two-digit years and everything else are ambiguous: never guess.
    raise AmbiguousDateError(f"Cannot resolve date '{s}' unambiguously")


# ---------------------------------------------------------------- cell normalization
def wildcard_tokens(table_id: str | None, column: str) -> frozenset[str]:
    w = aux("wildcards")
    toks = {norm_string(t) for t in w.get("global_tokens", [])}
    extra = (w.get("per_column") or {}).get(table_id or "", {}).get(column, [])
    toks |= {norm_string(str(t)) for t in extra}
    return frozenset(toks)


def _raw_text(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, float):
        return repr(raw)
    if isinstance(raw, (datetime, date)):
        return raw.isoformat()
    return str(raw)


def normalize_cell(
    raw: Any,
    *,
    role: ColumnRole,
    ctype: ColumnType,
    coord: str,
    tokens: frozenset[str],
    number_format: str | None = None,
    flags: frozenset[str] = frozenset(),
    percent: bool = False,
    date_order: str = "US_MDY",
) -> CellValue:
    fl = set(flags)
    rt = _raw_text(raw)

    def mk(kind: Literal["VALUE", "WILDCARD", "BLANK"], value: str | None) -> CellValue:
        # model_construct: values are produced here, so validation would only cost time
        return CellValue(kind=kind, value=value, raw=rt, coord=coord, flags=frozenset(fl))

    # Excel error values
    if isinstance(raw, str) and raw.strip().upper() in EXCEL_ERRORS:
        fl.add("EXCEL_ERROR")
        return mk("VALUE", raw.strip().upper())

    is_blank = raw is None or (isinstance(raw, str) and not raw.strip())
    is_key = role in (ColumnRole.KEY, ColumnRole.RANGE_MIN, ColumnRole.RANGE_MAX)

    if is_blank:
        if is_key and "" in tokens:
            return mk("WILDCARD", None)
        return mk("BLANK", None)

    if is_key and isinstance(raw, str) and norm_string(raw) in tokens:
        return mk("WILDCARD", None)

    if role == ColumnRole.IGNORE:
        return mk("VALUE", norm_string(str(raw), upper=False))

    # booleans
    if ctype == ColumnType.BOOLEAN:
        w = aux("wildcards")
        s = norm_string(str(raw))
        if isinstance(raw, bool):
            return mk("VALUE", "TRUE" if raw else "FALSE")
        if s in {norm_string(x) for x in w.get("boolean_true", [])}:
            return mk("VALUE", "TRUE")
        if s in {norm_string(x) for x in w.get("boolean_false", [])}:
            return mk("VALUE", "FALSE")
        fl.add("UNPARSEABLE")
        return mk("VALUE", s)

    if ctype == ColumnType.DATE:
        try:
            iso, has_time = parse_date(raw, date_order)
            if has_time:
                fl.add("DATE_HAS_TIME")
            return mk("VALUE", iso)
        except AmbiguousDateError:
            fl.add("AMBIGUOUS_DATE")
            return mk("VALUE", norm_string(str(raw)))

    if is_key and number_format and re.fullmatch(r"0+", number_format) and isinstance(raw, (int, float)):
        fl.add(f"NF:{number_format}")       # remembered so a STRING export column can restore zero padding

    # numerics
    if ctype in (ColumnType.DECIMAL, ColumnType.INTEGER) or role == ColumnRole.FACTOR:
        d = to_decimal(raw, allow_sci=ctype == ColumnType.DECIMAL or role == ColumnRole.FACTOR, percent=percent)
        if d is None:
            fl.add("UNPARSEABLE")
            return mk("VALUE", norm_string(str(raw)))
        if isinstance(raw, str) and "%" in raw and not percent:
            fl.add("PERCENT_SIGN_IGNORED")
        return mk("VALUE", fmt_decimal(d))

    # string / key
    if isinstance(raw, bool):
        return mk("VALUE", "TRUE" if raw else "FALSE")
    if isinstance(raw, (datetime, date)):
        return mk("VALUE", raw.isoformat()[:10])
    if isinstance(raw, (int, float, Decimal)):
        d = to_decimal(raw)
        assert d is not None
        text = fmt_decimal(d)
        if ctype == ColumnType.STRING and number_format and re.fullmatch(r"0+", number_format) and "." not in text:
            text = text.zfill(len(number_format))
        elif ctype == ColumnType.STRING and role == ColumnRole.KEY and "." not in text:
            fl.add("POSSIBLE_LEADING_ZERO_LOSS")   # numeric cell in a STRING code column
        return mk("VALUE", text)
    s = norm_string(str(raw))
    if _NUM_RE.match(s) and not re.match(r"^[+-]?0\d", s):
        d = to_decimal(s)
        if d is not None:
            return mk("VALUE", fmt_decimal(d))
    return mk("VALUE", s)


def infer_type(values: list[Any]) -> ColumnType:
    """Heuristic column type for approved-side columns (no type row)."""
    seen = [v for v in values if v is not None and str(v).strip() != ""]
    if not seen:
        return ColumnType.STRING
    if all(isinstance(v, bool) for v in seen):
        return ColumnType.BOOLEAN
    if all(isinstance(v, (datetime, date)) for v in seen):
        return ColumnType.DATE
    nums = [to_decimal(v) for v in seen]
    if all(n is not None for n in nums):
        if any(isinstance(v, str) and re.match(r"^0\d", v.strip()) for v in seen):
            return ColumnType.STRING
        if all(n == n.to_integral_value() for n in nums if n is not None) and not any(
            isinstance(v, float) and v != int(v) for v in seen
        ):
            return ColumnType.INTEGER
        return ColumnType.DECIMAL
    return ColumnType.STRING
