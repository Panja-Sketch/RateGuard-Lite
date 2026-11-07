"""Plain-language wording for findings (D-027).

Every finding gets three short parts: what we found, why it matters, what to do. They are rendered from
templates in config/messages.yaml. Technical detail (rule ID, raw values, cell references) is kept apart
in :func:`technical_details` so reports can show it in a separate column or a collapsible section.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from .config import aux
from .model.findings import Finding

# Words that must never appear in the main text of a message (technical detail may use them).
BANNED_WORDS = ("canonical", "composite key", "quantize", "normalize", "normalise", "normalized", "NOT_EVALUATED", "regex", "hash")
RULE_ID_RE = re.compile(r"\b[A-Z]{2,}(?:_[A-Z0-9]+)+\b")

NO_BASELINE_LINE = "We did not check tables outside this change, because no 'before' export was provided."
BASELINE_DONE_LINE = "We also checked tables outside this change against the 'before' export: {b}."


class _Safe(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return ""


_LO = ("from", "min", "minimum", "low", "start")
_HI = ("to", "max", "maximum", "high", "end", "through")
_RANGE_RE = re.compile(r"^(?P<stem>.+?)\s+(?P<end>" + "|".join(_LO + _HI) + r")$", re.I)


def range_stem(header: str) -> tuple[str, bool] | None:
    """'Stories From' -> ('Stories', True) (True = lower end); not a range header -> None."""
    m = _RANGE_RE.match(header.strip())
    if not m:
        return None
    return m.group("stem"), m.group("end").lower() in _LO


def unit_word(stem: str) -> str:
    """Business word for a range column: the field_map label when one matches ('Number of Stories (Building screen)' -> 'stories')."""
    token = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")
    for canon, label in aux("field_map").get("fields", {}).items():
        if canon == token or canon.endswith("_" + token):
            text = re.sub(r"\s*\(.*?\)", "", str(label)).strip()
            return re.sub(r"^number of ", "", text, flags=re.I).lower()
    return stem.lower()


def _band_text(lo: str, hi: str, stem: str) -> str:
    unit = unit_word(stem)
    if hi in ("", "(blank)", "*", "blank", "any value"):
        return f"{lo} or more {unit}" if lo not in ("(blank)", "*") else f"any number of {unit}"
    if lo == hi:
        return f"{lo} {unit}"
    return f"{lo} to {hi} {unit}"


def pretty_key(key: str) -> str:
    """'Stories From=4, Stories To=6, Territory=9' -> '4 to 6 stories, Territory = 9' (range bands in business words)."""
    if not key:
        return ""
    parts = [p.strip() for p in key.split(", ")]
    items: list[tuple[str, str]] = []
    for p in parts:
        h, sep, v = p.partition("=")
        items.append((h.strip(), v.strip()) if sep else ("", p))
    out: list[str] = []
    used: set[int] = set()
    for i, (h, v) in enumerate(items):
        if i in used:
            continue
        rs = range_stem(h) if h else None
        if rs is not None:
            stem, is_lo = rs
            mate = next((j for j, (h2, _) in enumerate(items) if j != i and j not in used and h2 and (r2 := range_stem(h2)) is not None
                         and r2[0].lower() == stem.lower() and r2[1] != is_lo), None)
            if mate is not None:
                lo, hi = (v, items[mate][1]) if is_lo else (items[mate][1], v)
                used.add(mate)
                out.append(_band_text(lo, hi, stem))
                continue
        shown = "any value" if v == "*" else ("blank" if v == "(blank)" else v)
        out.append(f"{h} = {shown}" if h else v)
    return ", ".join(out)


def pretty_cell(ref: str) -> str:
    """'file.xlsx!Tab!C5' -> 'file.xlsx, tab Tab, cell C5'."""
    if not ref:
        return ""
    parts = ref.split("!")
    if len(parts) >= 3:
        return f"{parts[0]}, tab {parts[1]}, cell {parts[2]}"
    if len(parts) == 2:
        return f"{parts[0]}, tab {parts[1]}"
    return ref


def _dec(v: str) -> Decimal | None:
    try:
        return Decimal(v.strip().replace(",", "").rstrip("%"))
    except (InvalidOperation, AttributeError):
        return None


def numbers(approved: str, export: str) -> tuple[str, str, str]:
    """Return (sentence, change text, 'undercharged'/'overcharged'/'charged incorrectly')."""
    a, e = _dec(approved), _dec(export)
    base = f"Approved {approved}, PolicyCenter has {export}"
    if a is None or e is None:
        return base + ".", "", "charged incorrectly"
    if a == e:
        return base + ".", "", "charged incorrectly"
    word = "lower" if e < a else "higher"
    charged = "undercharged" if e < a else "overcharged"
    if a != 0:
        pct = abs((e - a) / a) * 100
        change = f"{pct:.1f}% {word}"
        return f"{base} ({change}).", change, charged
    return f"{base} ({word}).", word, charged


def where_text(f: Finding) -> str:
    name = f.table_name or f.table_id
    base = f"Table '{name}'" if name else "A table"
    k = pretty_key(f.key)
    return f"{base} ({k})" if k else base


def _context(f: Finding) -> dict[str, Any]:
    nums, change, charged = numbers(f.approved_value, f.export_value)
    k = pretty_key(f.key)
    name = f.table_name or f.table_id or "this table"
    return _Safe(
        table=f"'{name}'", table_plain=name, where=where_text(f), key=k, column=f.column or "factor",
        approved=f.approved_value, export=f.export_value, numbers=nums, change=change, charged=charged,
        who=f"Policies with {k}" if k else "Policies that use this table",
        approved_cell=pretty_cell(f.approved_ref), export_cell=pretty_cell(f.export_ref),
        rows=f.details.get("rows_changed", ""),
    )


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def apply_plain(f: Finding) -> Finding:
    """Fill plain_what / plain_why / plain_action (only the parts the caller did not set) and the older text fields."""
    tpl = aux("messages").get(f.rule_id)
    if not tpl:
        return f
    ctx = _context(f)
    if not f.plain_what:
        f.plain_what = _clean(str(tpl.get("what", "")).format_map(ctx))
    if not f.plain_why:
        f.plain_why = _clean(str(tpl.get("why", "")).format_map(ctx))
    if not f.plain_action:
        f.plain_action = _clean(str(tpl.get("action", "")).format_map(ctx))
    f.explanation = _clean(f"{f.plain_what} {f.plain_why}")
    f.suggested_fix = f.plain_action
    return f


def technical_details(f: Finding) -> str:
    bits = [f"Rule: {f.rule_id}"]
    if f.table_id:
        bits.append(f"Table ID: {f.table_id}")
    if f.key:
        bits.append(f"Key: {f.key}")
    if f.column:
        bits.append(f"Column: {f.column}")
    if f.approved_value or f.export_value:
        bits.append(f"Approved value: {f.approved_value or '-'}; export value: {f.export_value or '-'}")
    if f.approved_ref:
        bits.append(f"Approved cell: {f.approved_ref}")
    if f.export_ref:
        bits.append(f"Export cell: {f.export_ref}")
    if f.message:
        bits.append(f"Detail: {f.message}")
    for k, v in sorted(f.details.items()):
        if k != "hint":
            bits.append(f"{k}: {v}")
    return " | ".join(bits)


def one_line(f: Finding) -> str:
    return f.plain_what or f"{where_text(f)}: {f.message}"
