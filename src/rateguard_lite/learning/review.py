"""Applying user decisions on proposals (spec 9.3). Used by `rgl review`, `rgl map` and the UI.

Only allow-listed kinds can be learned; nothing here touches comparison math or verdicts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..mapping.identity import name_key
from .store import LearningStore


def load_proposals(run_dir: Path) -> list[dict[str, Any]]:
    p = Path(run_dir) / "proposals.json"
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def describe(p: dict[str, Any]) -> str:
    k = p.get("kind")
    if k == "TABLE_ALIAS" and p.get("status") == "NEEDS_MAPPING":
        return f"Approved table '{p.get('approved')}' has no confident match."
    if k == "TABLE_ALIAS" and p.get("export"):
        return f"Approved '{p.get('approved')}'  ->  export '{p.get('export')}' (similarity {p.get('score')})"
    if k == "TABLE_ALIAS":
        return f"Jira reference '{p.get('jira_text', '')}' matched {p.get('canonical_id')} by similarity."
    if k == "HIDDEN_SHEET":
        return f"Hidden sheet '{p.get('sheet')}' in {p.get('file')} was not used."
    if k == "LAYOUT":
        return f"Sheet '{p.get('sheet')}' in {p.get('file')}: {p.get('reason')}"
    return json.dumps(p)[:120]


def apply_decision(store: LearningStore, p: dict[str, Any], decision: str, choice: int | None = None, user: str | None = None) -> str:
    """decision: accept | reject. Returns a short message. choice indexes p['candidates'] for NEEDS_MAPPING."""
    if decision != "accept":
        return "rejected (nothing learned)"
    kind, lob, cid, run_id = p.get("kind"), p.get("lob") or "", p.get("canonical_id"), p.get("run_id")
    if kind == "TABLE_ALIAS":
        a_names = p.get("approved_names") or ([p["approved"]] if p.get("approved") else [])
        if p.get("status") == "NEEDS_MAPPING":
            cands = p.get("candidates") or []
            if choice is None or not (0 <= choice < len(cands)):
                return "no candidate chosen; nothing learned"
            e_names = cands[choice]["names"]
        else:
            e_names = p.get("export_names") or ([p["export"]] if p.get("export") else [])
        if not cid or not a_names or not e_names:
            return "this proposal cannot be learned automatically"
        n = 0
        for nm in list(a_names) + list(e_names):
            if nm:
                store.add("TABLE_ALIAS", name_key(nm), {"canonical_id": cid}, scope_lob=lob, status="CONFIRMED", user=user, run_id=run_id,
                          evidence={"name": nm})
                n += 1
        return f"confirmed table alias ({n} name(s) -> {cid}); saved to the team mappings file"
    if kind == "HIDDEN_SHEET":
        store.add("LAYOUT_FINGERPRINT", p["sheet"], {"hidden_sheet": True}, scope_lob=lob, status="CONFIRMED", user=user, run_id=run_id)
        return "hidden sheet will be used from now on; saved to the team mappings file"
    return "no automatic action for this proposal type in v1 (see the admin guide)"


def propose(store: LearningStore, p: dict[str, Any], user: str | None = None) -> int:
    """Skipped proposals are kept locally as PROPOSED (never in force) so they can be confirmed later with `rgl learn confirm`."""
    if p.get("kind") != "TABLE_ALIAS" or p.get("status") == "NEEDS_MAPPING" or not p.get("canonical_id"):
        return 0
    names = list(p.get("approved_names") or ([p["approved"]] if p.get("approved") else [])) + \
        list(p.get("export_names") or ([p["export"]] if p.get("export") else []))
    n = 0
    for nm in names:
        if nm:
            store.add("TABLE_ALIAS", name_key(nm), {"canonical_id": p["canonical_id"]}, scope_lob=p.get("lob") or "", status="PROPOSED",
                      user=user, run_id=p.get("run_id"), evidence={"name": nm})
            n += 1
    return n
