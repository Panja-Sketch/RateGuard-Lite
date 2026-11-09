"""D-030: group findings per table and say, in plain words, what the likely cause is.

Nothing here creates, removes or changes a finding, a severity, a verdict or an exit code. It only reads the findings and the
tables, and writes one grouped item per table with a count, a likely cause and the rows underneath. A cause is named only when
the evidence is strong (at least 80% of the affected cells agree). Otherwise the item just says the values differ.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Any

from ..config import aux
from ..mapping.tables import TablePair
from ..model.core import RateTable, Row
from ..model.findings import Finding
from .common import Index, KeyT, build_index, key_names, key_text, to_decimal_safe
from .factors import FactorResult

PROBLEM_LEVELS = ("BLOCKER", "CRITICAL", "HIGH", "NEEDS_MAPPING", "NEEDS_REVIEW")
VALUE_RULES = {"VALUE_MISMATCH", "ORDER_OF_MAGNITUDE", "TRANSPOSITION", "SIGN_FLIP"}
STRONG = 0.8                      # share of the affected cells that must agree before a cause is named
WHOLE_TABLE = 0.9                 # share of rows that differ before "the whole table differs" is said
MAX_ROWS_IN_JSON = 200

_NOUN = {
    "VALUE_MISMATCH": ("wrong value", "wrong values"), "ORDER_OF_MAGNITUDE": ("decimal-point error", "decimal-point errors"),
    "TRANSPOSITION": ("value with swapped digits", "values with swapped digits"), "SIGN_FLIP": ("sign error", "sign errors"),
    "BLANK_FACTOR": ("blank factor", "blank factors"), "MISSING_ROW_IN_EXPORT": ("missing row", "missing rows"),
    "EXTRA_ROW_IN_EXPORT": ("extra row", "extra rows"), "WILDCARD_MISMATCH": ("wildcard row problem", "wildcard row problems"),
    "DUPLICATE_KEY": ("repeated row", "repeated rows"), "RANGE_GAP": ("gap between bands", "gaps between bands"),
    "RANGE_OVERLAP": ("overlap between bands", "overlaps between bands"), "OFF_BY_ONE_BOUNDARY": ("band edge off by one", "band edges off by one"),
}


@dataclass
class Problem:
    table_id: str
    table_name: str
    level: str
    count: int
    rules: dict[str, int]
    headline: str
    likely_cause: str = ""
    cause_kind: str = ""
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "table_id": self.table_id, "table_name": self.table_name, "level": self.level, "count": self.count, "rules": dict(self.rules),
            "headline": self.headline, "likely_cause": self.likely_cause, "cause_kind": self.cause_kind,
            "rows": [{"rule_id": f.rule_id, "level": f.level, "key": f.key, "column": f.column, "approved_value": f.approved_value,
                      "export_value": f.export_value, "what": f.plain_what or f.message} for f in self.findings[:MAX_ROWS_IN_JSON]],
            "rows_not_shown": max(0, len(self.findings) - MAX_ROWS_IN_JSON),
        }


# ------------------------------------------------------------------ helpers
def _val(row: Row | None, name: str) -> Decimal | None:
    if row is None:
        return None
    c = row.factors.get(name)
    return to_decimal_safe(c.value) if c is not None and c.kind == "VALUE" else None


def _label_map() -> dict[str, str]:
    out: dict[str, str] = {}
    for canon, aliases in (aux("synonyms").get("code_labels") or {}).items():
        group = [canon, *list(aliases or [])]
        key = _squash(str(canon))
        for g in group:
            out[_squash(str(g))] = key
    return out


_SUFFIX = re.compile(r"^\$?(\d+(?:\.\d+)?)([km])$", re.I)


def _squash(s: str) -> str:
    return re.sub(r"[\s_\-]+", "", s).upper()


def _norm_value(v: str, labels: dict[str, str]) -> str:
    """Candidate normalisation: $ and commas, k/M suffixes, case, spaces/underscores/hyphens, known code labels."""
    s = v.strip().replace(",", "")
    m = _SUFFIX.match(s)
    try:
        if m:
            n = Decimal(m.group(1)) * (1000 if m.group(2).lower() == "k" else 1_000_000)
            return format(n.normalize(), "f")
        return format(Decimal(s.replace("$", "")).normalize(), "f")
    except InvalidOperation:
        pass
    sq = _squash(s.replace("$", ""))
    return labels.get(sq, sq)


def _raw_key(row: Row) -> str:
    return " / ".join((c.raw or c.value or "(blank)") for c in row.key)


def _factor_sig(row: Row, names: list[str]) -> tuple[Decimal | None, ...]:
    return tuple(_val(row, n) for n in names)


# ------------------------------------------------------------------ diagnoses
def _diagnose_keys(pair: TablePair, fr: FactorResult) -> tuple[str, str] | None:
    """(a) Missing and extra rows that pair up once the key text is normalised, or that carry the same factors with one label different."""
    a, e = pair.approved, pair.export
    if a is None or e is None or fr.a_idx is None or fr.e_idx is None or not fr.missing or not fr.extra:
        return None
    a_idx, e_idx = fr.a_idx, fr.e_idx
    names = sorted(c.canonical_name for c in a.factor_columns())
    labels = _label_map()

    def nk(k: KeyT) -> tuple[str, ...]:
        return tuple(("*" if kind == "WILDCARD" else _norm_value(val or "", labels)) for kind, val in k)

    by_norm: dict[tuple[str, ...], list[KeyT]] = {}
    for ek in fr.extra:
        by_norm.setdefault(nk(ek), []).append(ek)
    by_mask: dict[tuple[int, KeyT, tuple[Decimal | None, ...]], list[KeyT]] = {}
    for ek in fr.extra:
        sig = _factor_sig(e_idx.rows[ek], names)
        for i in range(len(ek)):
            by_mask.setdefault((i, ek[:i] + (("", None),) + ek[i + 1:], sig), []).append(ek)
    used: set[KeyT] = set()
    pairs: list[tuple[KeyT, KeyT, str]] = []
    for mk in fr.missing:
        hit = [x for x in by_norm.get(nk(mk), []) if x not in used]
        kind = "N"
        if len(hit) != 1:
            hit, kind = [], "F"
            sig = _factor_sig(a_idx.rows[mk], names)
            for i in range(len(mk)):
                cand = [x for x in by_mask.get((i, mk[:i] + (("", None),) + mk[i + 1:], sig), []) if x not in used and x != mk]
                if len(cand) == 1:
                    hit = cand
                    break
        if len(hit) == 1:
            used.add(hit[0])
            pairs.append((mk, hit[0], kind))
    if len(pairs) < STRONG * max(len(fr.missing), len(fr.extra)):
        return None
    kinds = {k for _, _, k in pairs}
    mk, ek, _ = pairs[0]
    ex_a, ex_e = _raw_key(a_idx.rows[mk]), _raw_key(e_idx.rows[ek])
    example = f"for example '{ex_a}' in the approved file and '{ex_e}' in PolicyCenter"
    if kinds == {"N"}:
        return ("KEY_FORMAT", f"The same rows are written differently in the two files ({example}). The factors are identical, so this looks like a formatting difference, not a wrong value.")
    ratios = [SequenceMatcher(None, _raw_key(a_idx.rows[m]).lower(), _raw_key(e_idx.rows[x]).lower()).ratio() for m, x, _ in pairs]
    if sum(1 for r in ratios if r >= 0.8) >= STRONG * len(pairs):
        return ("KEY_TYPO", f"The row labels differ by a character or two ({example}), and the factors are identical. This looks like a typo in a label.")
    return ("KEY_LABEL", f"The same rows carry different labels in the two files ({example}). The factors are identical, so one file probably uses a code where the other uses a name.")


def _cells(pair: TablePair, fr: FactorResult, fs: list[Finding]) -> list[tuple[KeyT, str, Decimal, Decimal]]:
    a = pair.approved
    if a is None or fr.a_idx is None or fr.e_idx is None:
        return []
    hdr = {c.source_header: c.canonical_name for c in a.factor_columns()}
    kt: dict[str, KeyT] | None = None
    out: list[tuple[KeyT, str, Decimal, Decimal]] = []
    for f in fs:
        if f.rule_id not in VALUE_RULES:
            continue
        if kt is None:
            kt = {key_text(a, k): k for k in fr.a_idx.rows}
        k, fname = kt.get(f.key), hdr.get(f.column)
        if k is None or fname is None:
            continue
        ad, ed = _val(fr.a_idx.rows[k], fname), _val(fr.e_idx.rows.get(k), fname)
        if ad is not None and ed is not None:
            out.append((k, fname, ad, ed))
    return out


def _diagnose_values(pair: TablePair, fr: FactorResult, fs: list[Finding], others: list[tuple[RateTable, Index]]) -> tuple[str, str] | None:
    cells = _cells(pair, fr, fs)
    if not cells or fr.a_idx is None or fr.e_idx is None or pair.approved is None:
        return None
    n = len(cells)
    a_idx = fr.a_idx
    # (d) the values equal the 'before' export: the change was not loaded
    if pair.baseline is not None:
        b_idx = build_index(pair.baseline)
        hit = sum(1 for k, f, _, ed in cells if _val(b_idx.rows.get(k), f) == ed)
        if hit >= STRONG * n:
            return ("NOT_LOADED", f"The values in PolicyCenter equal the 'before' export ({hit} of {n}). This change looks as if it was never loaded.")
    # (c) the values equal another factor column of the same table
    names = sorted({f for _, f, _, _ in cells})
    all_f = sorted(c.canonical_name for c in pair.approved.factor_columns())
    if len(all_f) > 1:
        src = {c.canonical_name: c.source_header for c in pair.approved.factor_columns()}
        for col in names:
            mine = [(k, ed) for k, f, _, ed in cells if f == col]
            if len(mine) < 2:
                continue
            for other in all_f:
                if other == col:
                    continue
                hit = sum(1 for k, ed in mine if _val(a_idx.rows.get(k), other) == ed)
                if hit >= STRONG * len(mine):
                    return ("COLUMNS_SWAPPED", f"The '{src[col]}' column in PolicyCenter holds the approved '{src[other]}' values ({hit} of {len(mine)}). The columns look swapped or copied.")
    # (b) the values equal another table's approved values at the same rows
    if n >= 3:
        mine_names = key_names(pair.approved)
        best: tuple[int, str] = (0, "")
        for t, idx in others:
            if key_names(t) != mine_names:
                continue
            fmap = {c.canonical_name for c in t.factor_columns()}
            hit = 0
            for k, f, _, ed in cells:
                row = idx.rows.get(k)
                if row is None:
                    continue
                theirs = f if f in fmap else (next(iter(fmap)) if len(fmap) == 1 else None)
                if theirs is not None and _val(row, theirs) == ed:
                    hit += 1
            if hit > best[0]:
                best = (hit, t.display_name)
        if best[0] >= STRONG * n and best[0] >= 3:
            return ("COPIED_FROM", f"The values look copied from the approved table '{best[1]}' ({best[0]} of {n} wrong values equal that table's values at the same rows).")
    # (e) every row differs: the whole table, not single cells
    rows_bad = len({k for k, _, _, _ in cells})
    if len(a_idx.rows) >= 5 and rows_bad >= WHOLE_TABLE * len(a_idx.rows):
        return ("WHOLE_TABLE", f"Every row of this table differs ({rows_bad} of {len(a_idx.rows)}), not just single values. That usually means a different table, state or edition was loaded.")
    return None


# ------------------------------------------------------------------ grouping
def _headline(name: str, fs: list[Finding]) -> str:
    if len(fs) == 1:
        return fs[0].plain_what or fs[0].message
    counts: dict[str, int] = {}
    for f in fs:
        counts[f.rule_id] = counts.get(f.rule_id, 0) + 1
    parts = []
    for rule, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        one, many = _NOUN.get(rule, ("other problem", "other problems"))
        parts.append(f"{c} {one if c == 1 else many}")
    shown = ", ".join(parts[:3]) + (f" and {len(parts) - 3} more kinds" if len(parts) > 3 else "")
    return f"Table '{name}': {shown}."


def build_problems(findings: list[Finding], pairs: list[TablePair], fres: dict[str, FactorResult]) -> list[Problem]:
    by_id = {p.id: p for p in pairs}
    groups: dict[str, list[Finding]] = {}
    for f in findings:
        if f.level in PROBLEM_LEVELS:
            groups.setdefault(f.table_id or f"\0{id(f)}", []).append(f)
    cache: dict[str, Index] = {}
    out: list[Problem] = []
    for tid, fs in groups.items():
        name = fs[0].table_name or fs[0].table_id
        likely, kind = "", ""
        pair, fr = by_id.get(tid), fres.get(tid)
        if pair is not None and fr is not None and pair.approved is not None and pair.export is not None and len(fs) > 1:
            d = None
            if any(f.rule_id in VALUE_RULES for f in fs):
                others = []
                for p in pairs:
                    if p.id != tid and p.approved is not None:
                        if p.id not in cache:
                            cache[p.id] = build_index(p.approved)
                        others.append((p.approved, cache[p.id]))
                d = _diagnose_values(pair, fr, fs, others)
            if d is None and any(f.rule_id in ("MISSING_ROW_IN_EXPORT", "EXTRA_ROW_IN_EXPORT") for f in fs):
                d = _diagnose_keys(pair, fr)
            if d is None and any(f.rule_id in VALUE_RULES for f in fs):
                likely, kind = "The values differ from the approved ones. No common pattern was found.", "VALUES_DIFFER"
            elif d is not None:
                kind, likely = d
        worst = min(fs, key=lambda f: f.sort_key()).level
        rules: dict[str, int] = {}
        for f in fs:
            rules[f.rule_id] = rules.get(f.rule_id, 0) + 1
        out.append(Problem(fs[0].table_id, name, worst, len(fs), rules, _headline(name, fs), likely, kind, fs))
    out.sort(key=lambda p: (p.findings[0].sort_key()[0], p.table_id, p.table_name))
    return out


def headline_counts(problems: list[Problem]) -> dict[str, Any]:
    tables = [p for p in problems if p.table_id]
    other = [p for p in problems if not p.table_id]
    cells = sum(p.count for p in problems)
    if not problems:
        return {"problem_tables": 0, "problem_cells": 0, "problem_other": 0, "headline": ""}
    t = len(tables)
    text = f"{t} table{'s' if t != 1 else ''} {'have' if t != 1 else 'has'} problems ({sum(p.count for p in tables)} cell{'s' if sum(p.count for p in tables) != 1 else ''})"
    if other:
        text += f" and {len(other)} other problem{'s' if len(other) != 1 else ''}"
    if not tables:
        text = f"{len(other)} problem{'s' if len(other) != 1 else ''}"
    return {"problem_tables": t, "problem_cells": cells, "problem_other": len(other), "headline": text + "."}
