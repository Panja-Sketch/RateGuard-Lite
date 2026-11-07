"""Table mapping approved <-> export <-> baseline <-> Jira (spec 6). Deterministic and explainable."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from rapidfuzz import fuzz

from ..config import Config
from ..ingest.jira import JiraScope, JiraTableRef, TableAction
from ..learning.context import LearnedContext
from ..model.core import ColumnRole, RateBook, RateTable
from .identity import expand_tokens, extract_number, name_key

Confidence = Literal["CONFIRMED", "SUGGESTED", "UNMAPPED", "NONE"]
PLAUSIBLE = 0.20      # below this an unmatched approved table is reported as missing, not "needs mapping" (D-007)


@dataclass
class TablePair:
    id: str
    display: str
    approved: RateTable | None = None
    export: RateTable | None = None
    baseline: RateTable | None = None
    confidence: Confidence = "NONE"        # approved<->export confidence
    score: float = 0.0
    reason: str = ""
    candidates: list[tuple[str, float]] = field(default_factory=list)
    jira: list[JiraTableRef] = field(default_factory=list)
    jira_action: str | None = None          # effective action, "CONFLICT", or None
    jira_confidence: str = "UNRESOLVED"

    @property
    def native_export_id(self) -> str | None:
        return self.export.canonical_id if self.export else None


@dataclass
class MappingResult:
    pairs: list[TablePair]
    proposals: list[dict[str, object]] = field(default_factory=list)
    resolved_jira: list[JiraTableRef] = field(default_factory=list)

    def by_id(self) -> dict[str, TablePair]:
        return {p.id: p for p in self.pairs}


# ------------------------------------------------------------------ names & scoring
def _names(t: RateTable) -> list[str]:
    if t.origin == "APPROVED":
        return [t.display_name]
    md = t.metadata
    out = [md.get("table_name", ""), md.get("table_code", "")]
    if not any(out):
        out = [t.display_name]
    out.append(md.get("sheet_name", ""))
    return [n for n in out if n]


def _tokens(name: str, lob: str) -> str:
    toks = [x for x in expand_tokens(name) if x != lob.upper()]
    return " ".join(sorted(set(toks)))


def _number(t: RateTable) -> str | None:
    for strict in (True, False):
        for n in _names(t):
            v = extract_number(n, strict=strict)
            if v:
                return v
    return None


def _key_cols(t: RateTable) -> set[str]:
    return {c.canonical_name for c in t.columns if c.role in (ColumnRole.KEY, ColumnRole.RANGE_MIN, ColumnRole.RANGE_MAX)}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if (a or b) else 0.0


def score_pair(a: RateTable, e: RateTable, lob: str, learned: LearnedContext | None) -> tuple[float, dict[str, float], bool]:
    a_names, e_names = _names(a), _names(e)
    alias = 0.0
    if learned and learned.table_aliases:
        aids = {learned.alias_for(lob, name_key(n)) for n in a_names} - {None}
        eids = {learned.alias_for(lob, name_key(n)) for n in e_names} - {None}
        alias = 1.0 if aids & eids else 0.0
    na, ne = _number(a), _number(e)
    number = 1.0 if (na and ne and na == ne) else 0.0
    sim = 0.0
    identical = False
    for x in a_names:
        tx = _tokens(x, lob)
        for y in e_names:
            ty = _tokens(y, lob)
            if not tx or not ty:
                continue
            s = fuzz.token_set_ratio(tx, ty) / 100.0
            sim = max(sim, s)
            if tx == ty:
                identical = True
    cols = _jaccard(_key_cols(a), _key_cols(e))
    total = 0.45 * alias + 0.20 * number + 0.20 * sim + 0.15 * cols
    exact = (number == 1.0 and sim >= 0.85) or (identical and cols >= 0.5 and (na == ne or not (na and ne)))
    return total, {"alias": alias, "number": number, "name": round(sim, 3), "columns": round(cols, 3)}, bool(exact or alias)


def map_tables(approved: RateBook | None, export: RateBook, baseline: RateBook | None, jira: JiraScope | None,
               lob: str, cfg: Config, learned: LearnedContext | None = None) -> MappingResult:
    pairs: dict[str, TablePair] = {}
    exp_used: set[str] = set()
    proposals: list[dict[str, object]] = []

    if approved is not None:
        a_list = list(approved.tables.values())
        e_list = list(export.tables.values())
        scored: dict[str, list[tuple[float, str, dict[str, float], bool]]] = {}
        for a in a_list:
            row = []
            for e in e_list:
                s, parts, exact = score_pair(a, e, lob, learned)
                row.append((s, e.canonical_id, parts, exact))
            row.sort(key=lambda x: (-x[0], x[1]))
            scored[a.canonical_id] = row
        # exact-identity matches first; a tie for the same approved table is not exact (fail closed)
        taken_a: set[str] = set()
        for a in sorted(a_list, key=lambda t: t.canonical_id):
            row = scored[a.canonical_id]
            exact_rows = [r for r in row if r[3] and r[1] not in exp_used]
            if len(exact_rows) == 1 or (len(exact_rows) > 1 and exact_rows[0][0] - exact_rows[1][0] >= cfg.mapping.suggest_margin):
                s, eid, parts, _ = exact_rows[0]
                e = export.tables[eid]
                alias = parts["alias"] == 1.0
                pairs[a.canonical_id] = TablePair(a.canonical_id, a.display_name, a, e, None, "CONFIRMED", s,
                                                  "alias" if alias else "number+name" if parts["number"] else "identical name",
                                                  [(r[1], r[0]) for r in row[:3]])
                exp_used.add(eid)
                taken_a.add(a.canonical_id)
        for a in sorted(a_list, key=lambda t: t.canonical_id):
            if a.canonical_id in taken_a:
                continue
            row = [r for r in scored[a.canonical_id] if r[1] not in exp_used]
            cands = [(r[1], round(r[0], 3)) for r in row[:3]]
            best = row[0] if row else None
            second = row[1][0] if len(row) > 1 else 0.0
            if best and best[0] >= cfg.mapping.suggest_threshold and best[0] - second >= cfg.mapping.suggest_margin:
                e = export.tables[best[1]]
                pairs[a.canonical_id] = TablePair(a.canonical_id, a.display_name, a, e, None, "SUGGESTED", best[0],
                                                  "similarity", cands)
                exp_used.add(best[1])
                proposals.append({"kind": "TABLE_ALIAS", "status": "PROPOSED", "approved": a.display_name, "export": e.display_name,
                                  "approved_names": _names(a), "export_names": _names(e), "score": round(best[0], 3),
                                  "canonical_id": a.canonical_id})
            else:
                conf: Confidence = "UNMAPPED" if (best and best[0] >= PLAUSIBLE) else "NONE"
                pairs[a.canonical_id] = TablePair(a.canonical_id, a.display_name, a, None, None, conf, best[0] if best else 0.0,
                                                  "no confident match", cands)
                if conf == "UNMAPPED":
                    proposals.append({"kind": "TABLE_ALIAS", "status": "NEEDS_MAPPING", "approved": a.display_name,
                                      "approved_names": _names(a), "canonical_id": a.canonical_id,
                                      "candidates": [{"id": cid, "score": sc, "names": _names(export.tables[cid])} for cid, sc in cands]})
    # remaining export tables
    for eid, e in sorted(export.tables.items()):
        if eid in exp_used:
            continue
        pairs[f"E::{eid}"] = TablePair(eid, e.display_name, None, e, None, "NONE")
    # attach baseline by exact table identity
    if baseline is not None:
        by_native = {p.export.canonical_id: p for p in pairs.values() if p.export}
        by_code = {p.export.metadata.get("table_code", ""): p for p in pairs.values() if p.export and p.export.metadata.get("table_code")}
        for bid, b in sorted(baseline.tables.items()):
            p = by_native.get(bid) or by_code.get(b.metadata.get("table_code", "") or "\0")
            if p is not None and p.baseline is None:
                p.baseline = b
            else:
                pairs[f"B::{bid}"] = TablePair(bid, b.display_name, None, None, b, "NONE")
    result = MappingResult(list(pairs.values()), proposals)
    if jira is not None:
        resolve_jira(result, jira, lob, cfg)
    return result


# ------------------------------------------------------------------ Jira resolution
def _pair_names(p: TablePair) -> list[str]:
    out: list[str] = []
    for t in (p.approved, p.export, p.baseline):
        if t is not None:
            out.extend(_names(t))
    return out


def _pair_numbers(p: TablePair) -> set[str]:
    return {n for t in (p.approved, p.export, p.baseline) if t is not None and (n := _number(t))}


def resolve_jira(res: MappingResult, jira: JiraScope, lob: str, cfg: Config) -> None:
    pairs = res.pairs
    resolved: list[JiraTableRef] = []
    for ref in jira.tables:
        cand: list[tuple[float, TablePair, bool]] = []
        rtok = _tokens(ref.table_name or "", lob)
        for p in pairs:
            nums = _pair_numbers(p)
            num_hit = bool(ref.table_number and ref.table_number in nums)
            sim, ident = 0.0, False
            if rtok:
                for n in _pair_names(p):
                    pt = _tokens(n, lob)
                    if pt:
                        sim = max(sim, fuzz.token_set_ratio(rtok, pt) / 100.0)
                        ident = ident or pt == rtok
            if ref.table_number and nums and ref.table_number not in nums:
                continue                                        # a different, known table number
            score = (0.6 if num_hit else 0.0) + 0.4 * sim
            if ident and not num_hit:
                score = 0.6 + 0.4 * sim
            if score > 0:
                cand.append((score, p, num_hit or ident))
        cand.sort(key=lambda x: (-x[0], x[1].id))
        cid, conf = None, "UNRESOLVED"
        if cand:
            top = cand[0]
            second = cand[1][0] if len(cand) > 1 else 0.0
            if top[2] and top[0] >= 0.6 and top[0] - second >= cfg.mapping.suggest_margin:
                cid = top[1].id
                conf = "CONFIRMED" if top[1].confidence == "CONFIRMED" or top[1].approved is None else "SUGGESTED"
            elif top[0] >= 0.4 * cfg.mapping.suggest_threshold and top[0] - second >= 0.4 * cfg.mapping.suggest_margin:
                cid, conf = top[1].id, "SUGGESTED"
        ref2 = ref.model_copy(update={"canonical_id": cid, "confidence": conf})
        resolved.append(ref2)
        if cid:
            tp = next(p for p in pairs if p.id == cid)
            tp.jira.append(ref2)
    # effective action per pair
    for p in pairs:
        acts = {r.action.value for r in p.jira if r.action != TableAction.MENTION}
        if len(acts) > 1:
            p.jira_action = "CONFLICT"
        elif acts:
            p.jira_action = next(iter(acts))
        elif p.jira:
            p.jira_action = "MENTION"
        confs = {r.confidence for r in p.jira}
        p.jira_confidence = "SUGGESTED" if "SUGGESTED" in confs else ("CONFIRMED" if confs else "UNRESOLVED")
    res.resolved_jira = resolved


def harmonize_types(a: RateTable, e: RateTable, cfg: Config) -> RateTable:
    """Re-read approved cells using the export's authoritative column types (BOOLEAN/DATE/STRING).

    The approved workbook has no type row, so a Y/N key column is first read as text; the
    export declares BOOLEAN. Comparing them as-is would report phantom missing/extra rows.
    """
    from dataclasses import replace

    from ..ingest.normalize import normalize_cell
    from ..model.core import ColumnType

    e_types = {c.canonical_name: c.type for c in e.columns}
    redo = {c.canonical_name: e_types[c.canonical_name] for c in a.columns
            if c.canonical_name in e_types and c.type != e_types[c.canonical_name]
            and e_types[c.canonical_name] in (ColumnType.BOOLEAN, ColumnType.DATE, ColumnType.STRING)
            and c.role.value in ("KEY", "RANGE_MIN", "RANGE_MAX")}
    if not redo:
        return a
    kcols = a.key_columns()
    new_rows = []
    for r in a.rows:
        key = list(r.key)
        for i, c in enumerate(kcols):
            if c.canonical_name in redo:
                old = r.key[i]
                tokens = frozenset({"*", "ALL", "ANY", "DEFAULT", "<ALL>", ""})
                nf = next((f[3:] for f in old.flags if f.startswith("NF:")), None)
                raw: object = old.raw
                if redo[c.canonical_name] == ColumnType.STRING and nf and old.raw and old.raw.lstrip("-").isdigit():
                    raw = int(old.raw)            # normalize_cell restores the zero padding from the number format
                key[i] = normalize_cell(raw, role=c.role, ctype=redo[c.canonical_name], coord=old.coord, tokens=tokens,
                                        number_format=nf, flags=old.flags, date_order=cfg.dates.order)
        new_rows.append(replace(r, key=tuple(key)))
    cols = tuple(replace(c, type=redo[c.canonical_name]) if c.canonical_name in redo else c for c in a.columns)
    return replace(a, columns=cols, rows=tuple(new_rows))
