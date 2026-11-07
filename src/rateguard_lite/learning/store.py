"""Learning store (spec 9, D-024). Never silent, always user-confirmed.

CONFIRMED items live in the repo-level team file (config/team_mappings.yaml, see team.py) and are shared through git.
The local SQLite store keeps only PROPOSED items and run history. (Rows with status CONFIRMED from an older database
are still honoured so nothing breaks; the team file wins on conflict.)

Learning changes how inputs are READ AND MAPPED. It never changes comparison math, tolerances,
severities, or turns a FAIL into a PASS.
"""

from __future__ import annotations

import getpass
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ..config import data_dir
from ..errors import RGLError
from . import team
from .context import LearnedContext

KINDS = {"TABLE_ALIAS", "COLUMN_SYNONYM", "COLUMN_ROLE", "WILDCARD_TOKEN", "LAYOUT_FINGERPRINT", "RANGE_CONVENTION",
         "JIRA_PATTERN", "FINDING_DISPOSITION"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS learned_item(
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, scope_lob TEXT, scope_table TEXT, key TEXT NOT NULL,
  value_json TEXT NOT NULL, status TEXT NOT NULL, created_by TEXT, created_at TEXT, confirmed_by TEXT, confirmed_at TEXT,
  source_run_id TEXT, evidence_json TEXT, version INTEGER NOT NULL DEFAULT 1, supersedes INTEGER);
CREATE TABLE IF NOT EXISTS run(
  id TEXT PRIMARY KEY, started_at TEXT, tool_version TEXT, config_hash TEXT, input_hashes_json TEXT, verdict TEXT, metrics_json TEXT);
CREATE TABLE IF NOT EXISTS finding_disposition(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, rule_id TEXT, table_id TEXT, key TEXT, column_name TEXT,
  disposition TEXT NOT NULL, reason TEXT, decided_by TEXT, decided_at TEXT);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def current_user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return "unknown"


class LearningStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else data_dir() / "learning.db"
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------ items
    def add(self, kind: str, key: str, value: dict[str, Any], *, scope_lob: str | None = None, scope_table: str | None = None,
            status: str = "PROPOSED", user: str | None = None, run_id: str | None = None, evidence: dict[str, Any] | None = None) -> int | str:
        """PROPOSED -> a row in the local store (returns its int id). CONFIRMED -> an entry in the team file (returns 'T-xxxxxx')."""
        if kind not in KINDS:
            raise RGLError(f"'{kind}' is not a learnable item kind.", next_step=f"Allowed: {', '.join(sorted(KINDS))}")
        user = user or current_user()
        if status == "CONFIRMED":
            tid = team.upsert(kind, key, value, scope_lob=scope_lob, scope_table=scope_table, user=user, source_run_id=run_id)
            self._mark_promoted(kind, key, scope_lob, scope_table, value)
            return tid
        dup = self.db.execute("SELECT id FROM learned_item WHERE kind=? AND key=? AND IFNULL(scope_lob,'')=IFNULL(?,'') AND "
                              "IFNULL(scope_table,'')=IFNULL(?,'') AND status='PROPOSED' AND value_json=?",
                              (kind, key, scope_lob, scope_table, json.dumps(value, sort_keys=True))).fetchone()
        if dup:
            return int(dup["id"])
        cur = self.db.execute(
            "INSERT INTO learned_item(kind,scope_lob,scope_table,key,value_json,status,created_by,created_at,source_run_id,evidence_json,version,supersedes) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (kind, scope_lob, scope_table, key, json.dumps(value, sort_keys=True), "PROPOSED", user, now(), run_id,
             json.dumps(evidence or {}), 1, None))
        self.db.commit()
        return int(cur.lastrowid or 0)

    def _mark_promoted(self, kind: str, key: str, scope_lob: str | None, scope_table: str | None, value: dict[str, Any]) -> None:
        self.db.execute("UPDATE learned_item SET status='PROMOTED' WHERE kind=? AND key=? AND IFNULL(scope_lob,'')=IFNULL(?,'') AND "
                        "IFNULL(scope_table,'')=IFNULL(?,'') AND status='PROPOSED' AND value_json=?",
                        (kind, key, scope_lob, scope_table, json.dumps(value, sort_keys=True)))
        self.db.commit()

    def confirm(self, item_id: int, user: str | None = None) -> str:
        """Promote a PROPOSED row into the team file. Returns the team item id."""
        user = user or current_user()
        row = self.db.execute("SELECT * FROM learned_item WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise RGLError(f"Learned item {item_id} not found.", next_step="Run `rgl learn list --status PROPOSED`.")
        if row["status"] != "PROPOSED":
            raise RGLError(f"Item {item_id} is {row['status']}, not PROPOSED.")
        tid = team.upsert(row["kind"], row["key"], json.loads(row["value_json"]), scope_lob=row["scope_lob"], scope_table=row["scope_table"],
                          user=user, source_run_id=row["source_run_id"])
        self.db.execute("UPDATE learned_item SET status='PROMOTED', confirmed_by=?, confirmed_at=? WHERE id=?", (user, now(), item_id))
        self.db.commit()
        return tid

    def retire(self, item_id: int | str, user: str | None = None) -> bool:
        """Retire a local proposal (int id) or remove a team entry (id like 'T-1a2b3c'). Returns False if nothing matched."""
        if isinstance(item_id, str) and item_id.startswith("T-"):
            return team.remove(item_id)
        cur = self.db.execute("UPDATE learned_item SET status='RETIRED', confirmed_by=COALESCE(confirmed_by, ?) WHERE id=?",
                              (user or current_user(), int(item_id)))
        self.db.commit()
        return cur.rowcount > 0

    def team_items(self) -> list[dict[str, Any]]:
        """Confirmed team entries with a stable id (T-xxxxxx)."""
        return [dict(it, id=team.item_id(it["kind"], it["key"], it.get("scope_lob"), it.get("scope_table")), source="team")
                for it in team.load_items()]

    def confirmed_items(self) -> list[dict[str, Any]]:
        """Everything in force: team entries, plus any legacy CONFIRMED rows from an older database."""
        out = self.team_items()
        have = {(i["kind"], i.get("scope_lob") or "", i.get("scope_table") or "", i["key"]) for i in out}
        for r in self.list(status="CONFIRMED"):
            if (r["kind"], r["scope_lob"] or "", r["scope_table"] or "", r["key"]) in have:
                continue
            out.append({"kind": r["kind"], "scope_lob": r["scope_lob"], "scope_table": r["scope_table"], "key": r["key"],
                        "value": json.loads(r["value_json"]), "confirmed_by": r["confirmed_by"] or r["created_by"],
                        "confirmed_on": (r["confirmed_at"] or "")[:10], "id": r["id"], "source": "legacy-db"})
        return out

    def list(self, kind: str | None = None, status: str | None = None) -> list[sqlite3.Row]:
        q, args = "SELECT * FROM learned_item WHERE 1=1", []
        if kind:
            q += " AND kind=?"
            args.append(kind)
        if status:
            q += " AND status=?"
            args.append(status)
        return list(self.db.execute(q + " ORDER BY id", args))

    def get(self, item_id: int) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self.db.execute("SELECT * FROM learned_item WHERE id=?", (item_id,)).fetchone()
        return row

    def version_stamp(self) -> str:
        r = self.db.execute("SELECT COUNT(*) c, IFNULL(MAX(id),0) m, IFNULL(MAX(confirmed_at),'') t FROM learned_item WHERE status='CONFIRMED'").fetchone()
        base = f"{r['c']}.{r['m']}.{r['t']}"
        ts = team.stamp()
        return f"{base}+team:{ts}" if ts else base

    # ------------------------------------------------------------ run context
    def context(self, lob: str | None) -> LearnedContext:
        ctx = LearnedContext(version=self.version_stamp())
        items = self.confirmed_items()
        for it in [i for i in items if i["source"] != "team"] + [i for i in items if i["source"] == "team"]:   # team entries last: they win
            if it.get("scope_lob") not in (None, "", lob) and lob is not None:
                continue
            val, kind, key, tbl = it["value"], it["kind"], it["key"], it.get("scope_table")
            used = {"id": it["id"], "version": 1, "kind": kind, "key": key, "source": it["source"]}
            if kind == "TABLE_ALIAS":
                ctx.table_aliases[(it.get("scope_lob") or lob or "", key)] = val["canonical_id"]
            elif kind == "COLUMN_SYNONYM":
                ctx.column_synonyms[key] = val["to"]
            elif kind == "COLUMN_ROLE" and tbl:
                ctx.column_roles.setdefault(tbl, {})[key] = val["role"]
            elif kind == "WILDCARD_TOKEN" and tbl:
                toks = ctx.wildcard_tokens.setdefault(tbl, {}).setdefault(key, [])
                if val["token"] not in toks:
                    toks.append(val["token"])
            elif kind == "RANGE_CONVENTION" and tbl:
                ctx.range_conventions[tbl] = val["convention"]
            elif kind == "JIRA_PATTERN":
                ctx.jira_patterns[key] = val["action"]
            elif kind == "LAYOUT_FINGERPRINT":
                if val.get("hidden_sheet"):
                    ctx.hidden_sheets_ok.add(key)
            else:
                continue
            ctx.used.append(used)
        return ctx

    # ------------------------------------------------------------ runs / dispositions
    def record_run(self, run_id: str, tool_version: str, config_hash: str, inputs: dict[str, Any], verdict: str, metrics: dict[str, Any]) -> None:
        self.db.execute("INSERT OR REPLACE INTO run VALUES (?,?,?,?,?,?,?)",
                        (run_id, now(), tool_version, config_hash, json.dumps(inputs, sort_keys=True), verdict, json.dumps(metrics, sort_keys=True)))
        self.db.commit()

    def record_disposition(self, run_id: str, rule_id: str, table_id: str, key: str, column: str, disposition: str, reason: str,
                           user: str | None = None) -> None:
        self.db.execute("INSERT INTO finding_disposition(run_id,rule_id,table_id,key,column_name,disposition,reason,decided_by,decided_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (run_id, rule_id, table_id, key, column, disposition, reason, user or current_user(), now()))
        self.db.commit()

    def hint_for(self, rule_id: str, table_id: str, key: str, column: str) -> str | None:
        r = self.db.execute("SELECT * FROM finding_disposition WHERE rule_id=? AND table_id=? AND key=? AND column_name=? AND disposition='FALSE_POSITIVE' "
                            "ORDER BY id DESC LIMIT 1", (rule_id, table_id, key, column)).fetchone()
        if r:
            return f"previously marked false positive by {r['decided_by']} on {r['decided_at'][:10]}: {r['reason']}"
        return None

    # ------------------------------------------------------------ team sharing
    def export_yaml(self, out: Path) -> int:
        items = [{"kind": i["kind"], "scope_lob": i.get("scope_lob"), "scope_table": i.get("scope_table"), "key": i["key"], "value": i["value"]}
                 for i in self.confirmed_items()]
        Path(out).write_text(yaml.safe_dump({"rateguard_lite_mappings": 1, "items": items}, sort_keys=False), encoding="utf-8")
        return len(items)

    def import_yaml(self, path: Path, trust: bool = False, user: str | None = None) -> int:
        """Another team's mappings file: PROPOSED by default (confirm each with `rgl learn confirm`); --trust writes them to the team file."""
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if data.get("rateguard_lite_mappings") != 1:
            raise RGLError("Not a RateGuard-Lite mappings file.", where=Path(path).name)
        n = 0
        for it in data.get("items", []):
            self.add(it["kind"], it["key"], it["value"], scope_lob=it.get("scope_lob"), scope_table=it.get("scope_table"),
                     status="CONFIRMED" if trust else "PROPOSED", user=user)
            n += 1
        return n

    # ------------------------------------------------------------ metrics (spec 9.4)
    def stats(self) -> dict[str, Any]:
        runs = list(self.db.execute("SELECT * FROM run ORDER BY started_at"))
        auto, total, manual = 0, 0, []
        for r in runs:
            m = json.loads(r["metrics_json"] or "{}")
            auto += m.get("tables_confirmed", 0)
            total += m.get("tables_mapped_total", 0)
            manual.append(m.get("manual_mappings", 0))
        items = self.list()
        proposed = sum(1 for i in items if i["status"] != "PROPOSED")
        confirmed = sum(1 for i in items if i["status"] in ("CONFIRMED", "RETIRED", "PROMOTED"))
        fp = {row[0]: row[1] for row in self.db.execute("SELECT rule_id, COUNT(*) FROM finding_disposition WHERE disposition='FALSE_POSITIVE' GROUP BY rule_id")}
        tot_disp = {row[0]: row[1] for row in self.db.execute("SELECT rule_id, COUNT(*) FROM finding_disposition GROUP BY rule_id")}
        med = sorted(manual)[len(manual) // 2] if manual else 0
        return {
            "runs": len(runs),
            "auto_mapped_rate": (auto / total) if total else None,
            "suggestions_total": len(items),
            "suggestion_acceptance_rate": (confirmed / (confirmed + sum(1 for i in items if i["status"] == "PROPOSED"))) if items else None,
            "false_positive_rate_by_rule": {k: fp.get(k, 0) / v for k, v in tot_disp.items()},
            "median_manual_mappings_per_run": med,
            "manual_mappings_over_time": manual,
            "_proposed_decided": proposed,
        }
