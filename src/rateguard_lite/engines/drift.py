"""Drift mode: export vs export, exact equality (spec 8.6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import Config
from ..mapping.tables import map_tables
from ..model.core import RateBook
from .common import build_index, key_names, key_text, ref, table_hash


@dataclass
class DriftResult:
    identical: list[str] = field(default_factory=list)
    differing: list[str] = field(default_factory=list)
    only_left: list[str] = field(default_factory=list)
    only_right: list[str] = field(default_factory=list)
    diffs: list[dict[str, Any]] = field(default_factory=list)
    left_meta: dict[str, str] = field(default_factory=dict)
    right_meta: dict[str, str] = field(default_factory=dict)
    needs_mapping: list[str] = field(default_factory=list)


def run_drift(left: RateBook, right: RateBook, cfg: Config, lob: str) -> DriftResult:
    res = DriftResult(left_meta=left.metadata, right_meta=right.metadata)
    m = map_tables(left, right, None, None, lob, cfg)
    for p in m.pairs:
        if p.approved is not None and p.export is not None:
            a, e = p.approved, p.export
            if key_names(a) != key_names(e) or sorted(c.canonical_name for c in a.factor_columns()) != sorted(c.canonical_name for c in e.factor_columns()):
                res.differing.append(p.id)
                res.diffs.append({"table": p.display, "type": "STRUCTURE", "key": "", "column": "", "left": "columns differ", "right": "", "left_ref": "", "right_ref": ""})
                continue
            if table_hash(a) == table_hash(e):
                res.identical.append(p.id)
                continue
            ai, ei = build_index(a), build_index(e)
            n0 = len(res.diffs)
            for k, ar in ai.rows.items():
                er = ei.rows.get(k)
                if er is None:
                    res.diffs.append({"table": p.display, "type": "ROW_ONLY_LEFT", "key": key_text(a, k), "column": "", "left": "present", "right": "absent",
                                      "left_ref": ref(a, ar.key[0] if ar.key else None), "right_ref": ""})
                    continue
                for n, ac in ar.factors.items():
                    ec = er.factors.get(n)
                    if ec is not None and (ac.kind, ac.value) != (ec.kind, ec.value):
                        res.diffs.append({"table": p.display, "type": "VALUE", "key": key_text(a, k), "column": n, "left": ac.raw or "", "right": ec.raw or "",
                                          "left_ref": ref(a, ac), "right_ref": ref(e, ec)})
            for k, er in ei.rows.items():
                if k not in ai.rows:
                    res.diffs.append({"table": p.display, "type": "ROW_ONLY_RIGHT", "key": key_text(e, k), "column": "", "left": "absent", "right": "present",
                                      "left_ref": "", "right_ref": ref(e, er.key[0] if er.key else None)})
            (res.differing if len(res.diffs) > n0 else res.identical).append(p.id)
        elif p.approved is not None:
            (res.needs_mapping if p.confidence == "UNMAPPED" else res.only_left).append(p.id)
        elif p.export is not None:
            res.only_right.append(p.id)
    return res
